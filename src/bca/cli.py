"""Command line entry point: ``bca``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskID, TimeRemainingColumn
from rich.table import Table

from bca.config import ConfigurationError, Settings, get_settings
from bca.ingest import endpoints
from bca.ingest.client import FeedRequest, HockeyTechClient
from bca.ingest.errors import IngestError

if TYPE_CHECKING:
    from bca.analytics.special_teams import Agreement
    from bca.ingest.backfill import Backfill

app = typer.Typer(
    name="bca",
    help="Bearcat Hockey Analytics - ACHA data ingestion and analysis.",
    no_args_is_help=True,
    add_completion=False,
)
feed_app = typer.Typer(help="Talk to the ACHA / HockeyTech LeagueStat feed.")
app.add_typer(feed_app, name="feed")
db_app = typer.Typer(help="Manage the local database.")
app.add_typer(db_app, name="db")
ingest_app = typer.Typer(help="Archive the feed and load it into the database.")
app.add_typer(ingest_app, name="ingest")
audit_app = typer.Typer(help="Reconcile the data and measure scorekeeping completeness (M2).")
app.add_typer(audit_app, name="audit")
special_app = typer.Typer(help="Derive power-play and penalty-kill numbers from penalties (M3).")
app.add_typer(special_app, name="special-teams")
scouting_app = typer.Typer(help="Build opponent scouting reports (M4).")
app.add_typer(scouting_app, name="scouting")
rankings_app = typer.Typer(help="Rebuild and check the ACHA M1 computer ranking (M6).")
app.add_typer(rankings_app, name="rankings")
export_app = typer.Typer(help="Export the stored pages as a static site's data (M7).")
app.add_typer(export_app, name="export")

console = Console()


def open_client(settings: Settings | None = None) -> HockeyTechClient:
    """Build a feed client, reporting misconfiguration as advice not a traceback.

    The contact-email guard is the most likely thing to stop a new contributor,
    and its message is the fix. A stack trace would bury it.
    """
    try:
        return HockeyTechClient(settings or get_settings())
    except ConfigurationError as exc:
        console.print(f"\n[bold red]Cannot send requests.[/bold red]\n\n{exc}\n")
        raise typer.Exit(code=2) from exc


@app.callback()
def _main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
    )
    # One line per request drowns a 9,000-request backfill. Kept under -v.
    logging.getLogger("httpx").setLevel(logging.DEBUG if verbose else logging.WARNING)


# --- the nine verified endpoints, as a smoke-testable catalogue -------------


def _probe_catalogue() -> dict[str, FeedRequest]:
    """One representative request per verified endpoint.

    The game and player ids are real: game 33169 is Cincinnati 7-2 Kent State
    (2026-09-18) and player 33548 is a UC skater.
    """
    season = endpoints.CURRENT_MENS_SEASON_ID
    uc = endpoints.UC_TEAM_ID
    return {
        "seasons": endpoints.seasons(),
        "teams_by_season": endpoints.teams_by_season(season),
        "team_schedule": endpoints.team_schedule(uc, season),
        "team_roster": endpoints.team_roster(uc, season),
        "game_summary": endpoints.game_summary(33169),
        "play_by_play": endpoints.play_by_play(33169),
        "skater_stats": endpoints.skater_stats(season, uc),
        "goalie_stats": endpoints.goalie_stats(season, uc),
        "player_game_log": endpoints.player_game_log(33548, season),
    }


@feed_app.command("check")
def feed_check(
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass the disk cache.")] = False,
    out_dir: Annotated[
        Path | None,
        typer.Option("--out-dir", help="Also write each payload here as JSON."),
    ] = None,
) -> None:
    """Fetch every verified endpoint and report what came back.

    This is the project's canary. If the ACHA rotates its API key or changes a
    view, this is what tells us -- loudly, and before a backfill half-succeeds.
    """
    settings = get_settings()
    console.print(
        f"[dim]feed[/dim] {settings.acha_feed_base_url}  "
        f"[dim]client_code[/dim] {settings.acha_client_code}  "
        f"[dim]delay[/dim] {settings.request_delay_seconds}s"
    )

    table = Table("endpoint", "result", "bytes", "source", "top-level shape")
    failures: list[tuple[str, str]] = []

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    with open_client(settings) as client:
        for name, request in _probe_catalogue().items():
            try:
                response = client.fetch(request, use_cache=not no_cache)
            except IngestError as exc:
                failures.append((name, str(exc)))
                table.add_row(name, "[red]FAIL[/red]", "-", "-", str(exc)[:60])
                continue

            status = "[yellow]EMPTY[/yellow]" if response.is_empty else "[green]OK[/green]"
            if response.is_empty:
                failures.append((name, "returned an empty payload"))

            table.add_row(
                name,
                status,
                f"{len(response.raw_text):,}",
                "cache" if response.from_cache else "network",
                _describe(response.payload),
            )

            if out_dir is not None:
                target = out_dir / f"{name}.json"
                target.write_text(json.dumps(response.payload, indent=2), encoding="utf-8")

    console.print(table)

    if failures:
        console.print(f"\n[red]{len(failures)} endpoint(s) did not return usable data:[/red]")
        for name, reason in failures:
            console.print(f"  [red]*[/red] {name}: {reason}")
        raise typer.Exit(code=1)

    console.print("\n[green]All 9 verified endpoints returned data.[/green]")


@feed_app.command("get")
def feed_get(
    feed: Annotated[str, typer.Argument(help="modulekit | statviewfeed | gc")],
    params: Annotated[
        list[str],
        typer.Argument(help="Query parameters as key=value, e.g. view=seasons"),
    ],
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass the disk cache.")] = False,
) -> None:
    """Fetch an arbitrary feed resource and print it. For exploration."""
    parsed: dict[str, str] = {}
    for item in params:
        if "=" not in item:
            raise typer.BadParameter(f"expected key=value, got {item!r}")
        key, value = item.split("=", 1)
        parsed[key] = value

    request = FeedRequest(feed, parsed)
    with open_client() as client:
        response = client.fetch(request, use_cache=not no_cache)
    console.print_json(json.dumps(response.payload))


# --- database ----------------------------------------------------------------


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply all migrations to the database in BCA_DATABASE_URL."""
    from bca.db.migrate import upgrade

    upgrade()
    console.print("[green]Database is at the latest migration.[/green]")


# --- ingestion ---------------------------------------------------------------

SeasonOption = Annotated[
    list[int] | None,
    typer.Option(
        "--season",
        "-s",
        help="season_id to include (repeatable). Default: all seven men's seasons.",
    ),
]


def _seasons(selected: list[int] | None) -> list[int]:
    known = set(endpoints.MENS_REGULAR_SEASONS.values())
    chosen = selected or sorted(known)
    unknown = sorted(set(chosen) - known)
    if unknown:
        raise typer.BadParameter(f"not a men's regular season: {unknown}; known: {sorted(known)}")
    return chosen


@ingest_app.command("backfill")
def ingest_backfill(
    season: SeasonOption = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Fetch only teamsbyseason (one request per season) and show what would run.",
        ),
    ] = False,
    refresh_current: Annotated[
        bool,
        typer.Option(
            "--refresh-current",
            help="Re-fetch the current season's teams, schedules and rosters to pick up "
            "newly completed games. Previous versions are kept in the archive history.",
        ),
    ] = False,
    upstream_stats: Annotated[
        bool,
        typer.Option(
            "--upstream-stats",
            help="Also archive each MD1 team's season skater and goalie stats (2 requests "
            "per team-season), for the M2 audit to compare against. Never used as a source.",
        ),
    ] = False,
) -> None:
    """Archive every MD1 schedule, roster and completed box score.

    Resumable: anything already archived costs no request, so just re-run after
    an interruption. Writes nothing to the database; run `bca ingest normalize`
    next.
    """
    from bca.ingest.backfill import Backfill

    season_ids = _seasons(season)
    settings = get_settings()
    delay = settings.request_delay_seconds

    with open_client(settings) as client:
        if dry_run:
            _print_survey(Backfill(client), season_ids, delay)
            return

        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            current: dict[str, TaskID] = {}

            def on_stage(name: str, total: int, uncached: int) -> None:
                progress.console.print(
                    f"[bold]{name}[/bold]: {total:,} resources, {uncached:,} to fetch "
                    f"(~{uncached * delay / 60:.0f} min at {delay}s/request)"
                )
                current["task"] = progress.add_task(name, total=total)

            def on_resource(_stage: str, _request: FeedRequest, _cached: bool) -> None:
                if "task" in current:
                    progress.advance(current["task"])

            results = Backfill(client, on_stage=on_stage, on_resource=on_resource).run(
                season_ids, refresh_current=refresh_current, upstream_stats=upstream_stats
            )

    table = Table("season", "MD1 teams", "final games", "fetched", "from archive", "failed")
    for r in results:
        table.add_row(
            str(r.season_id),
            str(r.md1_teams),
            f"{r.final_games:,}",
            f"{r.fetched:,}",
            f"{r.from_archive:,}",
            f"[red]{len(r.failures)}[/red]" if r.failures else "0",
        )
    console.print(table)

    failures = [f for r in results for f in r.failures]
    for identity, reason in failures[:25]:
        console.print(f"  [red]*[/red] {identity}: {reason}")
    if len(failures) > 25:
        console.print(f"  ... and {len(failures) - 25} more (see the log)")
    console.print("\nNext: [bold]bca ingest normalize[/bold]")


def _print_survey(backfill: Backfill, season_ids: list[int], delay: float) -> None:
    table = Table("season", "teams", "MD1 teams", "MD1 divisions", "schedule+roster requests")
    total = 0
    for survey in backfill.survey(season_ids):
        needed = backfill.uncached_team_requests(survey)
        total += needed
        divisions = ", ".join(d.removeprefix("MD1 ") for d in survey.md1_divisions)
        table.add_row(
            str(survey.season_id),
            str(survey.total_teams),
            str(len(survey.md1_teams)),
            divisions or "[red]none[/red]",
            f"{needed:,}",
        )
    console.print(table)
    console.print(
        f"Schedules and rosters: {total:,} requests (~{total * delay / 60:.0f} min). "
        "Box scores are counted once schedules are archived: roughly one request per "
        "completed game.\n"
        "[yellow]Check that every season shows MD1 teams before running for real[/yellow]"
        " -- the MD1 rule was verified against 2026-27 only."
    )


@ingest_app.command("normalize")
def ingest_normalize(
    view: Annotated[
        list[str] | None,
        typer.Option(
            "--view",
            help="Load only this archive view (repeatable), e.g. --view players. Its "
            "dependencies must already be loaded. Default: everything.",
        ),
    ] = None,
    changed_since: Annotated[
        str | None,
        typer.Option(help="Load only archive entries written at or after this time "
                     "(ISO, e.g. 2026-10-24T07:00). Default: everything."),
    ] = None,
) -> None:
    """Load the archive into the database. Offline: sends no requests."""
    from datetime import datetime as _datetime

    from bca.db.session import make_engine, make_session_factory
    from bca.ingest.archive import RawArchive
    from bca.ingest.normalize import Normalizer

    settings = get_settings()
    archive = RawArchive(settings.resolved_cache_dir)
    sessions = make_session_factory(make_engine(settings=settings))
    try:
        since = _datetime.fromisoformat(changed_since) if changed_since else None
        tally = Normalizer(sessions, archive).run(view, changed_since=since)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    table = Table("view", "loaded", "failed")
    for name in ("seasons", "teamsbyseason", "schedule", "roster", "gameSummary", "players"):
        if view and name not in view:
            continue
        failed = tally.failed[name]
        table.add_row(name, f"{tally.loaded[name]:,}", f"[red]{failed}[/red]" if failed else "0")
    console.print(table)

    if tally.issues:
        summary = ", ".join(f"{stage} {n}" for stage, n in sorted(tally.issues.items()))
        console.print(f"Issues recorded: {summary}  (table ingest_issues)")
    if tally.issues.get("canary"):
        console.print(
            "[bold yellow]CANARY:[/bold yellow] a field the ACHA has never recorded is "
            "non-zero. Stop and investigate -- see tests/test_data_assumptions.py."
        )


@ingest_app.command("coverage")
def ingest_coverage(
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Also write the report here as JSON.")
    ] = None,
) -> None:
    """Per-season coverage of the archive and database. The M1 exit report."""
    from bca.db.session import make_engine, make_session_factory
    from bca.ingest.archive import RawArchive
    from bca.ingest.coverage import build_report

    settings = get_settings()
    sessions = make_session_factory(make_engine(settings=settings))
    with sessions() as session:
        report = build_report(session, RawArchive(settings.resolved_cache_dir))

    table = Table(
        "season", "MD1 teams", "scheduled", "final", "archived", "normalized", "issues", ""
    )
    for s in report.seasons:
        issues = ", ".join(f"{k} {v}" for k, v in sorted(s.issues.items())) or "-"
        table.add_row(
            s.season,
            str(s.md1_teams),
            f"{s.games_scheduled:,}",
            f"{s.games_final:,}",
            f"{s.summaries_archived:,}",
            f"{s.summaries_normalized:,}",
            issues,
            "[green]ok[/green]"
            if s.complete
            else "[red]gap[/red]"
            if s.fetched
            else "[dim]not fetched[/dim]",
        )
    console.print(table)

    for s in report.seasons:
        if s.missing_summaries:
            console.print(f"{s.season} final games with no box score: {s.missing_summaries}")
        if s.unloaded_summaries:
            console.print(f"{s.season} box scores that did not load: {s.unloaded_summaries}")
    if report.other_issues:
        console.print(f"Issues not tied to a game: {report.other_issues}")
    style = "bold yellow" if report.canary_findings else "green"
    console.print(f"[{style}]Canary findings: {report.canary_findings}[/{style}]")

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        console.print(f"Wrote {json_out}")


# --- audit -------------------------------------------------------------------


@audit_app.command("run")
def audit_run() -> None:
    """Rebuild reconciliation findings and completeness flags. Offline, idempotent."""
    from bca.audit.run import run_audit
    from bca.db.session import make_engine, make_session_factory

    sessions = make_session_factory(make_engine(settings=get_settings()))
    result = run_audit(sessions)

    table = Table("check", "findings", "unexplained")
    for check, n in sorted(result.findings.items()):
        unexplained = result.unexplained[check]
        table.add_row(check, f"{n:,}", f"[red]{unexplained:,}[/red]" if unexplained else "0")
    console.print(table)
    console.print(f"Completeness flags: {result.quality_rows:,} team-games")
    console.print("\nNext: [bold]bca audit report[/bold]")


@audit_app.command("report")
def audit_report(
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Also write the report here as JSON.")
    ] = None,
) -> None:
    """The M2 gate report: every check's pass and explanation rates, and completeness."""
    from bca.audit.report import SAFE_TO_COMPARE, build_report
    from bca.db.session import make_engine, make_session_factory

    sessions = make_session_factory(make_engine(settings=get_settings()))
    with sessions() as session:
        report = build_report(session)

    table = Table("check", "unit", "checked", "not checkable", "findings", "explained by",
                  "unexplained", "rate")
    for c in report.checks:
        explained = ", ".join(
            f"{k} {v:,}" for k, v in c.by_explanation.items() if k != "unexplained"
        ) or "-"
        table.add_row(
            c.check, c.unit, f"{c.checked:,}", f"{c.not_checkable:,}", f"{c.findings:,}",
            explained, f"{c.unexplained:,}", f"{c.unexplained_rate:.2%}",
        )
    console.print(table)

    table = Table("field", "entered", "not entered", "can't tell", "rate", "compare across teams")
    for f in report.completeness:
        safe = f.rate >= SAFE_TO_COMPARE
        table.add_row(
            f.field, f"{f.entered:,}", f"{f.not_entered:,}", f"{f.unknown:,}", f"{f.rate:.1%}",
            "[green]yes[/green]" if safe else "[yellow]flag per game[/yellow]",
        )
    console.print(table)

    seasons = Table("season", *(f.field for f in report.completeness))
    for season_id, rates in report.completeness_by_season.items():
        seasons.add_row(str(season_id), *(f"{rates.get(f.field, 0):.0%}"
                                          for f in report.completeness))
    console.print(seasons)

    console.print("\n[bold]Named cases (M2 exit criterion)[/bold]")
    console.print_json(data=report.named_cases)

    for c in report.checks:
        if c.sample_unexplained:
            console.print(f"\n[bold]{c.check}[/bold] unexplained, e.g.:")
            for line in c.sample_unexplained:
                console.print(f"  {line}")

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        console.print(f"Wrote {json_out}")


# --- special teams -------------------------------------------------------------


@special_app.command("run")
def special_teams_run() -> None:
    """Simulate every full box score's strength state and store the results. Offline."""
    from bca.analytics.special_teams import run_special_teams
    from bca.db.session import make_engine, make_session_factory

    sessions = make_session_factory(make_engine(settings=get_settings()))
    result = run_special_teams(sessions)

    skipped = ", ".join(f"{k} {v}" for k, v in sorted(result.skipped.items())) or "none"
    console.print(f"Simulated {result.games:,} games (skipped: {skipped}).")
    _print_agreement([result.agreement])
    if result.issues:
        console.print(f"\n[yellow]{len(result.issues)} simulation issues[/yellow], e.g.:")
        for game_id, issue in result.issues[:10]:
            console.print(f"  game {game_id}: {issue}")


@special_app.command("validate")
def special_teams_validate(
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Also write the comparison here as JSON.")
    ] = None,
) -> None:
    """Compare every rule combination with the scorekeepers' flags. Writes nothing to the DB."""
    from bca.analytics.special_teams import agreement, load, rule_grid
    from bca.db.session import make_engine, make_session_factory

    sessions = make_session_factory(make_engine(settings=get_settings()))
    with sessions() as session:
        league = load(session)
    results = [agreement(league, rules)[0] for rules in rule_grid()]
    _print_agreement(results)

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "rules": vars(a.rules),
                "penalty_instant_agreement": round(a.penalty_rate, 4),
                "goal_agreement": round(a.goal_rate, 4),
                "opportunities_exact": round(a.opportunity_exact, 4),
                "opportunities_within_one": round(a.opportunity_within_one, 4),
                "goal_confusion": {f"{s}->{f}": n for (s, f), n in sorted(a.goals.items())},
            }
            for a in results
        ]
        json_out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        console.print(f"Wrote {json_out}")


def _print_agreement(results: list[Agreement]) -> None:
    table = Table("coincidentals", "4-on-4", "opportunity", "double minor", "penalties",
                  "goals", "opps exact", "opps ±1")
    for a in results:
        r = a.rules
        table.add_row(
            r.coincidental, "yes" if r.coincidental_four_on_four else "no", r.opportunity,
            str(r.double_minor_opportunities), f"{a.penalty_rate:.2%}", f"{a.goal_rate:.2%}",
            f"{a.opportunity_exact:.2%}", f"{a.opportunity_within_one:.2%}",
        )
    console.print(table)


# --- scouting and serving ----------------------------------------------------------


@scouting_app.command("build")
def scouting_build(
    team: Annotated[
        list[int] | None,
        typer.Option("--team", "-t", help="team_id to build (repeatable). Default: every MD1 "
                     "team this season."),
    ] = None,
    season: Annotated[int, typer.Option("--season", "-s")] = endpoints.CURRENT_MENS_SEASON_ID,
) -> None:
    """Build and store scouting reports and their special-teams detail pages. Offline;
    the API serves exactly what this stores."""
    from bca.analytics.rankings import RANKED_SEASONS
    from bca.analytics.rankings_report import store_rankings_report
    from bca.analytics.scouting import store_reports
    from bca.analytics.team_detail import store_detail_reports
    from bca.db.session import make_engine, make_session_factory

    sessions = make_session_factory(make_engine(settings=get_settings()))
    try:
        reports = store_reports(sessions, season, team)
        details = store_detail_reports(sessions, season, team)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if season in RANKED_SEASONS:
        standing = store_rankings_report(sessions, season)
        console.print(f"Built the Where we stand page: {len(standing.rows)} teams, "
                      f"games through {standing.games_through}.")
    thin = [r for r in reports if r.current.record.gp < 5]
    console.print(f"Built {len(reports)} scouting reports and {len(details)} special-teams "
                  f"pages for season {season}.")
    if thin:
        console.print(f"[yellow]{len(thin)} have fewer than 5 games[/yellow]: "
                      + ", ".join(f"{r.team} ({r.current.record.gp})" for r in thin[:8]))
    console.print("\nServe them: [bold]bca serve[/bold], then the web app: "
                  "[bold]cd web && npm run dev[/bold]")


@rankings_app.command("fetch")
def rankings_fetch(
    url: Annotated[str, typer.Argument(help="An official M1 computer ranking PDF, from "
                                             "achahockey.org/m1-rankings.")],
    season: Annotated[int, typer.Option("--season", "-s")] = endpoints.CURRENT_MENS_SEASON_ID,
    as_of: Annotated[str | None, typer.Option(help="Override the PDF's own 'as of' date "
                                                   "(YYYY-MM-DD).")] = None,
) -> None:
    """Download one official ranking PDF (one request), archive it, parse and store it."""
    from datetime import date as _date

    from bca.analytics.acha_pdf import fetch_pdf, store_published
    from bca.analytics.rankings import load_ranking_season
    from bca.config import ConfigurationError
    from bca.db.session import make_engine, make_session_factory

    settings = get_settings()
    try:
        path = fetch_pdf(url, season, settings)
    except ConfigurationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    sessions = make_session_factory(make_engine(settings=settings))
    with sessions() as session:
        ranking_season = load_ranking_season(session, season)
        names = {t: ranking_season.names[t] for t in ranking_season.md1}
        try:
            stored = store_published(session, season, path, names,
                                     _date.fromisoformat(as_of) if as_of else None,
                                     fallback=ranking_season.names)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        session.commit()
    console.print(f"Archived {path.name} and stored the ranking as of {stored} "
                  f"(season {season}).")


@rankings_app.command("pickup")
def rankings_pickup(
    season: Annotated[int, typer.Option("--season", "-s")] = endpoints.CURRENT_MENS_SEASON_ID,
) -> None:
    """Archive and store any new ranking PDF in Downloads (BCA_RANKINGS_INBOX). No requests."""
    from bca.analytics.acha_pdf import pick_up_season
    from bca.db.session import make_engine, make_session_factory

    settings = get_settings()
    sessions = make_session_factory(make_engine(settings=settings))
    with sessions.begin() as session:
        results = pick_up_season(session, season, settings)
    if not results:
        console.print(f"Nothing new in {settings.rankings_inbox} for season {season}.")
    for r in results:
        if r.error:
            console.print(f"[red]Could not store {r.name}[/red]: {r.error}")
        else:
            console.print(f"Archived {r.name} and stored the ranking as of {r.as_of}.")
    if any(r.error for r in results):
        raise typer.Exit(1)


@rankings_app.command("build")
def rankings_build(
    season: Annotated[list[int] | None, typer.Option("--season", "-s", help="Repeatable. "
                                                     "Default: every 24-team season.")] = None,
) -> None:
    """Build and store the Where we stand page (also done by `bca scouting build`)."""
    from bca.analytics.rankings import RANKED_SEASONS
    from bca.analytics.rankings_report import store_rankings_report
    from bca.db.session import make_engine, make_session_factory

    sessions = make_session_factory(make_engine(settings=get_settings()))
    for season_id in season or RANKED_SEASONS:
        if season_id not in RANKED_SEASONS:
            raise typer.BadParameter(f"season {season_id} predates the 24-team field; "
                                     f"rankings pages exist for {RANKED_SEASONS}")
        report = store_rankings_report(sessions, season_id)
        uc = f"UC {report.uc.rank}" if report.uc else "UC not MD1"
        console.print(f"{report.season}: {len(report.rows)} teams, games through "
                      f"{report.games_through}, {len(report.checks)} official rankings, {uc}.")


@rankings_app.command("check")
def rankings_check(
    season: Annotated[int, typer.Option("--season", "-s")] = endpoints.CURRENT_MENS_SEASON_ID,
) -> None:
    """Rebuild the ranking as of each stored official PDF and compare, team by team.

    Exits 1 when a finished season's final ranking puts different teams above the
    nationals line: the rebuild can't be trusted to show where a team stands."""
    from itertools import groupby

    from sqlalchemy import select

    from bca.analytics.acha_pdf import compare
    from bca.analytics.rankings import load_ranking_season
    from bca.db import schema as db
    from bca.db.session import make_engine, make_session_factory

    sessions = make_session_factory(make_engine(settings=get_settings()))
    with sessions() as session:
        ranking_season = load_ranking_season(session, season)
        published = list(session.scalars(
            select(db.PublishedRanking).where(db.PublishedRanking.season_id == season)
            .order_by(db.PublishedRanking.as_of, db.PublishedRanking.rank)))
    if not published:
        console.print(f"No official ranking stored for season {season}; "
                      "fetch one with [bold]bca rankings fetch URL -s SEASON[/bold].")
        return
    checks = [compare(ranking_season, list(rows))
              for _, rows in groupby(published, key=lambda p: p.as_of)]
    for check in checks:
        console.print(
            f"[bold]{check.as_of}[/bold] (our games through {check.our_games_through}): "
            f"{check.agree_exact}/{check.teams} exact, {check.within_2} within 2, "
            f"top 24 {'match' if check.top24_match else '[red]DIFFER[/red]'}")
        for d in check.differences:
            console.print(f"    {d.team}: ACHA {d.official}, rebuilt {d.rebuilt} "
                          f"-- {d.cause or 'unexplained'}")
    final = checks[-1]
    if season != endpoints.CURRENT_MENS_SEASON_ID and not final.top24_match:
        console.print("[red]The final ranking's top 24 differs from the rebuild.[/red]")
        raise typer.Exit(1)


@export_app.command("static")
def export_static_cmd(
    out: Annotated[Path | None, typer.Option(help="Default: web/public/data")] = None,
) -> None:
    """Write every stored page as JSON files for the web app (then npm run dev or build)."""
    from bca.db.session import make_engine, make_session_factory
    from bca.export import DEFAULT_OUT, export_static

    sessions = make_session_factory(make_engine(settings=get_settings()))
    summary = export_static(sessions, out or DEFAULT_OUT)
    console.print(f"Exported {summary.files} files for seasons {summary.seasons}; "
                  f"data through {summary.data_through}.")


@app.command("nightly")
def nightly(
    deploy: Annotated[bool, typer.Option(help="Deploy to Vercel when the pages changed.")] = True,
    season: Annotated[int, typer.Option("--season", "-s")] = endpoints.CURRENT_MENS_SEASON_ID,
) -> None:
    """Refresh the current season, rebuild every page, export, and deploy (M7).

    Run each morning by Task Scheduler (scripts/nightly.ps1). Exit codes: 0 success or
    nothing to deploy, 1 a step failed, 3 the canary tripped (deploy withheld)."""
    from datetime import UTC
    from datetime import datetime as _datetime

    from bca.db.session import make_engine, make_session_factory
    from bca.ingest.archive import RawArchive
    from bca.nightly import log_to_file, real_hooks, run_nightly

    settings = get_settings()
    log = log_to_file(_datetime.now(UTC).astimezone().date())
    sessions = make_session_factory(make_engine(settings=settings))
    try:
        run = run_nightly(sessions, archive=RawArchive(settings.resolved_cache_dir),
                          hooks=real_hooks(settings, sessions), season=season, deploy=deploy)
    except Exception as exc:  # before a run could be recorded: the database never came up
        console.print(f"[red]Nightly run failed before it started: {exc}[/red]\nLog: {log}")
        raise typer.Exit(1) from exc
    style = {"success": "green", "skipped": "green", "canary": "bold yellow"}.get(run.status,
                                                                                    "red")
    console.print(f"[{style}]{run.status}[/{style}] at step {run.step}"
                  f"{' -- deployed' if run.deployed else ''}\n{run.message}\nLog: {log}")
    if run.status == "canary":
        raise typer.Exit(3)
    if run.status == "failed":
        raise typer.Exit(1)


#: The reports' JSON schemas, from which the web app's TypeScript types are generated.
REPORT_SCHEMA = Path(__file__).parents[2] / "web" / "src" / "types" / "report.schema.json"
DETAIL_SCHEMA = REPORT_SCHEMA.with_name("detail.schema.json")
RANKINGS_SCHEMA = REPORT_SCHEMA.with_name("rankings.schema.json")


@scouting_app.command("schema")
def scouting_schema() -> None:
    """Write the reports' JSON schemas for the web app (then: cd web && npm run gen:types)."""
    from bca.analytics.rankings_report import rankings_schema
    from bca.analytics.scouting import report_schema
    from bca.analytics.team_detail import detail_schema

    REPORT_SCHEMA.parent.mkdir(parents=True, exist_ok=True)
    REPORT_SCHEMA.write_text(report_schema(), encoding="utf-8")
    DETAIL_SCHEMA.write_text(detail_schema(), encoding="utf-8")
    RANKINGS_SCHEMA.write_text(rankings_schema(), encoding="utf-8")
    console.print(f"Wrote {REPORT_SCHEMA.name}, {DETAIL_SCHEMA.name} and "
                  f"{RANKINGS_SCHEMA.name}. "
                  "Next: cd web && npm run gen:types")


@app.command("serve")
def serve(
    port: Annotated[int, typer.Option(help="Port for the API.")] = 8000,
    reload: Annotated[bool, typer.Option(help="Reload on code changes.")] = False,
) -> None:
    """Run the read-only API locally (http://127.0.0.1:PORT/api/docs)."""
    import uvicorn

    uvicorn.run("bca.api.app:create_app", factory=True, host="127.0.0.1", port=port,
                reload=reload)


def _describe(payload: object, depth: int = 0) -> str:
    """A one-line description of a payload's shape, for the check table."""
    if isinstance(payload, dict):
        keys = list(payload)[:4]
        suffix = ", ..." if len(payload) > 4 else ""
        if depth == 0 and len(payload) == 1:
            only = keys[0]
            return f"{only} -> {_describe(payload[only], depth + 1)}"
        return "{" + ", ".join(keys) + suffix + "}"
    if isinstance(payload, list):
        inner = _describe(payload[0], depth + 1) if payload else ""
        return f"[{len(payload)} x {inner}]" if inner else "[]"
    return type(payload).__name__


if __name__ == "__main__":
    app()
