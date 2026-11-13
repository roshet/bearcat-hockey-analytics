"""Load the raw archive into the normalized schema. Entirely offline.

Separating this from fetching is what makes the archive worth having: a parser
fix found at game 6,000 is applied by re-running this, in minutes, without
sending a single request to the league's server.

Each archived resource is loaded in its own transaction. A resource that fails
to parse or load is rolled back and recorded in ``ingest_issues``; the run
carries on. Re-running is idempotent: every resource replaces what it loaded
last time, including its own issue rows.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from bca.db import schema as db
from bca.ingest.archive import ArchivedResponse, RawArchive
from bca.ingest.parsers import game_summary as gs
from bca.ingest.parsers import modulekit as mk
from bca.ingest.parsers import statview as sv

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Archive views in dependency order: games must exist before box scores.
_ORDER: tuple[tuple[str, str], ...] = (
    ("modulekit", "seasons"),
    ("modulekit", "teamsbyseason"),
    ("modulekit", "schedule"),
    ("modulekit", "roster"),
    ("statviewfeed", "gameSummary"),
    ("statviewfeed", "players"),
)

#: Child tables of ``games`` that a box score replaces wholesale.
_GAME_CHILDREN: tuple[type[db.Base], ...] = (
    db.GameTeamStats,
    db.PeriodStats,
    db.GoalAssist,
    db.GoalEvent,
    db.PenaltyEvent,
    db.GoalieAppearance,
    db.SkaterGameStats,
)


@dataclass
class NormalizeTally:
    """What one normalize run did, per archive view."""

    loaded: Counter[str] = field(default_factory=Counter)
    failed: Counter[str] = field(default_factory=Counter)
    issues: Counter[str] = field(default_factory=Counter)

    @property
    def total_failed(self) -> int:
        return sum(self.failed.values())


class _Unloadable(Exception):
    """The resource parsed but cannot be loaded consistently."""


def resource_name(entry: ArchivedResponse) -> str:
    """Stable identity of an archived resource, used to key its issues."""
    extras = sorted(f"{k}={v}" for k, v in entry.params.items() if k not in {"view", "tab"})
    return ":".join([entry.feed, entry.view, *extras])


class Normalizer:
    def __init__(self, sessions: sessionmaker[Session], archive: RawArchive) -> None:
        self.sessions = sessions
        self.archive = archive
        #: Last values written per player, so 300k box-score lines don't each
        #: cost a round trip. Updated only after the owning transaction commits.
        self._players: dict[int, tuple[str, str, str]] = {}
        self._pending_players: dict[int, tuple[str, str, str]] = {}
        self._issues: list[db.IngestIssue] = []

    # -- driver ------------------------------------------------------------------

    def run(self, views: Iterable[str] | None = None,
            changed_since: datetime | None = None) -> NormalizeTally:
        """Load every archived view, or only ``views`` (in dependency order).

        Loading a subset is safe only when the views it depends on are already
        loaded -- e.g. ``players`` alone after the league's season lines are
        refetched, without the ~35 minutes of reloading 6,400 box scores.

        ``changed_since`` loads only entries the archive wrote at or after that
        time (by file mtime): the nightly run's new and refetched responses,
        still in dependency order. Everything older is already in the database.
        """
        cutoff = changed_since.timestamp() if changed_since else None
        wanted = None if views is None else set(views)
        unknown = (wanted or set()) - {view for _, view in _ORDER}
        if unknown:
            raise ValueError(f"unknown views {sorted(unknown)}")
        tally = NormalizeTally()
        loaders: dict[str, Callable[[Session, ArchivedResponse], None]] = {
            "seasons": self._load_seasons,
            "teamsbyseason": self._load_teams,
            "schedule": self._load_schedule,
            "roster": self._load_roster,
            "gameSummary": self._load_game_summary,
            "players": self._load_player_stats,
        }
        for feed, view in _ORDER:
            if wanted is not None and view not in wanted:
                continue
            entries = self.archive.iter_entries(feed, view)
            if view == "teamsbyseason":
                # Latest season last, so team names end up current.
                entries = iter(sorted(entries, key=lambda e: int(e.params.get("season_id", 0))))
            for entry in entries:
                if cutoff is not None and entry.path.stat().st_mtime < cutoff:
                    continue
                self._load_one(entry, loaders[view], tally)
            logger.info(
                "%s: %d loaded, %d failed", view, tally.loaded[view], tally.failed[view]
            )
        return tally

    def _load_one(
        self,
        entry: ArchivedResponse,
        loader: Callable[[Session, ArchivedResponse], None],
        tally: NormalizeTally,
    ) -> None:
        resource = resource_name(entry)
        self._pending_players = {}
        self._issues = []
        failure: tuple[str, str] | None = None

        try:
            with self.sessions.begin() as session:
                session.execute(delete(db.IngestIssue).where(db.IngestIssue.resource == resource))
                loader(session, entry)
                session.add_all(self._finish_issues(resource, entry))
        except ValidationError as exc:
            failure = ("parse", _summarise_validation(exc))
        except (ValueError, _Unloadable) as exc:
            failure = ("parse" if isinstance(exc, ValueError) else "load", str(exc))
        except SQLAlchemyError as exc:
            failure = ("load", f"{type(exc).__name__}: {exc}".splitlines()[0])

        if failure is None:
            self._players.update(self._pending_players)
            tally.loaded[entry.view] += 1
            for issue in self._issues:
                tally.issues[issue.stage] += 1
            return

        stage, message = failure
        logger.warning("%s failed at %s: %s", resource, stage, message)
        tally.failed[entry.view] += 1
        tally.issues[stage] += 1
        with self.sessions.begin() as session:
            session.execute(delete(db.IngestIssue).where(db.IngestIssue.resource == resource))
            # A canary finding stands even when the rest of the game is unloadable.
            self._issues = [i for i in self._issues if i.stage == "canary"]
            tally.issues["canary"] += len(self._issues)
            self._issue(stage, message)
            session.add_all(self._finish_issues(resource, entry))

    # -- loaders -----------------------------------------------------------------

    def _load_seasons(self, session: Session, entry: ArchivedResponse) -> None:
        for season in mk.parse_seasons(entry.payload):
            session.merge(
                db.Season(
                    season_id=season.season_id,
                    name=season.name,
                    short_name=season.short_name,
                    is_playoff=season.is_playoff,
                    start_date=season.start_date,
                    end_date=season.end_date,
                )
            )

    def _load_teams(self, session: Session, entry: ArchivedResponse) -> None:
        season_id = _param_int(entry, "season_id")
        teams = self._dedupe(mk.parse_teams_by_season(entry.payload), lambda t: t.team_id, "team")
        for team in teams:
            session.merge(
                db.Team(
                    team_id=team.team_id, name=team.name, nickname=team.nickname, code=team.code
                )
            )
            session.merge(
                db.TeamSeason(
                    team_id=team.team_id,
                    season_id=season_id,
                    name=team.name,
                    division_id=team.division_id,
                    division_name=team.division_name,
                    is_md1=team.is_md1,
                )
            )

    def _load_schedule(self, session: Session, entry: ArchivedResponse) -> None:
        for game in mk.parse_schedule(entry.payload):
            existing = session.get(db.Game, game.game_id)
            row = existing or db.Game(game_id=game.game_id, summary_normalized=False)
            row.season_id = game.season_id
            row.game_date = game.date_played
            row.start_time = game.start_time
            row.home_team_id = game.home_team_id
            row.visiting_team_id = game.visiting_team_id
            row.home_goals = game.home_goals
            row.visiting_goals = game.visiting_goals
            row.is_final = game.is_final
            row.overtime = game.overtime
            row.shootout = game.shootout
            row.status = game.status
            row.venue = game.venue
            row.attendance = game.attendance or None
            if existing is None:
                session.add(row)

    def _load_roster(self, session: Session, entry: ArchivedResponse) -> None:
        team_id = _param_int(entry, "team_id")
        season_id = _param_int(entry, "season_id")
        roster = mk.parse_roster(entry.payload)
        key = (db.RosterEntry.team_id == team_id) & (db.RosterEntry.season_id == season_id)
        session.execute(delete(db.RosterEntry).where(key))
        session.execute(
            delete(db.StaffEntry).where(
                (db.StaffEntry.team_id == team_id) & (db.StaffEntry.season_id == season_id)
            )
        )

        for player in self._dedupe(roster.players, lambda p: p.player_id, "roster player"):
            self._upsert_player(
                session, player.player_id, player.first_name, player.last_name, player.position
            )
            session.add(
                db.RosterEntry(
                    team_id=team_id,
                    season_id=season_id,
                    player_id=player.player_id,
                    jersey_number=player.jersey_number,
                    position=player.position,
                    shoots=player.shoots,
                    catches=player.catches,
                    active=player.active,
                )
            )
        for member in self._dedupe(roster.staff, lambda s: (s.person_id, s.role), "staff entry"):
            session.add(
                db.StaffEntry(
                    team_id=team_id,
                    season_id=season_id,
                    person_id=member.person_id,
                    role=member.role,
                    first_name=member.first_name,
                    last_name=member.last_name,
                )
            )

    def _load_game_summary(self, session: Session, entry: ArchivedResponse) -> None:
        requested = _optional_param_int(entry, "game_id")
        for finding in gs.canary_findings(entry.payload):
            if requested is not None and gs.is_known_trial_finding(requested, finding):
                self._issue(
                    "data",
                    f"{finding} entered during the September 2023 trial; partial and "
                    "unusable, not loaded. See docs/data-sources.md.",
                )
                continue
            self._issue(
                "canary",
                f"{finding} is non-zero: the ACHA may have started recording this. "
                "See tests/test_data_assumptions.py.",
            )

        summary = gs.parse_game_summary(entry.payload)
        game_id = summary.details.game_id
        if requested is not None and requested != game_id:
            raise _Unloadable(f"requested game {requested} but the feed returned game {game_id}")

        game = session.get(db.Game, game_id)
        if game is None:
            raise _Unloadable(f"box score for game {game_id}, which no archived schedule lists")

        teams = (summary.home.info.team_id, summary.visiting.info.team_id)
        if teams != (game.home_team_id, game.visiting_team_id):
            raise _Unloadable(
                f"box score teams {teams} disagree with the schedule "
                f"{(game.home_team_id, game.visiting_team_id)}"
            )

        for table in _GAME_CHILDREN:
            session.execute(delete(table).where(table.game_id == game_id))  # type: ignore[attr-defined]
        session.flush()

        for block, is_home in ((summary.home, True), (summary.visiting, False)):
            self._load_team_block(session, game_id, block, is_home)

        for period in summary.periods:
            session.add(
                db.PeriodStats(
                    game_id=game_id,
                    period=period.period.number,
                    home_goals=period.stats.home_goals,
                    home_shots=period.stats.home_shots,
                    visiting_goals=period.stats.visiting_goals,
                    visiting_shots=period.stats.visiting_shots,
                )
            )

        goals = self._dedupe(summary.goals, lambda g: g.goal_id, "goal")
        if summary.is_score_only:
            self._issue(
                "data",
                "score-only box score: no lineup, scorers, goal times, shots, penalties "
                "or goalies entered; its zeros mean not entered",
            )
        else:
            untimed = sum(goal.seconds is None for goal in goals)
            if untimed:
                self._issue("data", f"{untimed} of {len(goals)} goals have no time entered")
        for goal in goals:
            self._load_goal(session, game_id, goal)

        for penalty in self._dedupe(summary.penalties, lambda p: p.penalty_id, "penalty"):
            for person in (penalty.taken_by, penalty.served_by):
                if person is not None:
                    self._upsert_person(session, person)
            session.add(
                db.PenaltyEvent(
                    game_id=game_id,
                    penalty_id=penalty.penalty_id,
                    team_id=penalty.team.team_id,
                    period=penalty.period.number,
                    seconds=penalty.seconds,
                    minutes=penalty.minutes,
                    description=penalty.description,
                    rule_number=penalty.rule_number,
                    taken_by_id=penalty.taken_by.player_id if penalty.taken_by else None,
                    served_by_id=penalty.served_by.player_id if penalty.served_by else None,
                    is_bench=penalty.is_bench,
                    upstream_is_power_play=penalty.upstream_is_power_play,
                )
            )

        game.summary_normalized = True
        game.box_score_kind = "score_only" if summary.is_score_only else "full"
        if game.attendance is None and summary.details.attendance:
            game.attendance = summary.details.attendance

    def _load_player_stats(self, session: Session, entry: ArchivedResponse) -> None:
        """The league's season lines for one team -- comparison data only.

        The season comes from the ``season`` parameter. An entry requested with
        ``season_id`` instead holds the *current* season whatever it says, so
        it fails here rather than load under the wrong season.
        """
        season_id = _param_int(entry, "season")
        team_id = _param_int(entry, "team_id")
        position = entry.params.get("position")
        if position == "skaters":
            kind = "skater"
            lines: list[sv.SkaterSeasonLine] | list[sv.GoalieSeasonLine] = (
                sv.parse_skater_stats(entry.payload)
            )
        elif position == "goalies":
            kind = "goalie"
            lines = sv.parse_goalie_stats(entry.payload)
        else:
            raise ValueError(f"unknown player stats position {position!r}")

        limit = _optional_param_int(entry, "limit")
        if limit is not None and len(lines) >= limit:
            raise _Unloadable(f"{len(lines)} rows fill the page (limit {limit}); may be truncated")

        key = db.UpstreamPlayerSeasonStats
        session.execute(
            delete(key).where(
                (key.season_id == season_id) & (key.team_id == team_id) & (key.kind == kind)
            )
        )
        for line in self._dedupe(lines, lambda line: line.player_id, f"upstream {kind} line"):
            row = db.UpstreamPlayerSeasonStats(
                season_id=season_id,
                team_id=team_id,
                player_id=line.player_id,
                kind=kind,
                name=line.name,
                position="G" if kind == "goalie" else getattr(line, "position", ""),
                division=line.division,
                games_played=line.games_played,
            )
            if isinstance(line, sv.SkaterSeasonLine):
                row.goals = line.goals
                row.assists = line.assists
                row.points = line.points
                row.penalty_minutes = line.penalty_minutes
                row.shootout_winning_goals = line.shootout_winning_goals
            elif isinstance(line, sv.GoalieSeasonLine):
                row.goals_against = line.goals_against
                row.saves = line.saves
                row.minutes_played = line.minutes_played
                row.shootout_losses = line.shootout_losses
            session.add(row)

    def _load_team_block(
        self, session: Session, game_id: int, block: gs.TeamBlock, is_home: bool
    ) -> None:
        team_id = block.info.team_id
        stats = block.stats
        session.add(
            db.GameTeamStats(
                game_id=game_id,
                team_id=team_id,
                is_home=is_home,
                shots=stats.shots,
                goals=stats.goals,
                pp_goals=stats.power_play_goals,
                pp_opportunities=stats.power_play_opportunities,
                penalty_minutes=stats.penalty_minutes,
                infractions=stats.infractions,
            )
        )
        lines = [s for s in block.skaters if s.player.player_id != gs.PLACEHOLDER_PLAYER_ID]
        for line in self._dedupe(lines, lambda s: s.player.player_id, "skater line"):
            self._upsert_person(session, line.player)
            session.add(
                db.SkaterGameStats(
                    game_id=game_id,
                    player_id=line.player.player_id,
                    team_id=team_id,
                    jersey_number=line.player.jersey_number,
                    position=line.player.position,
                    goals=line.stats.goals,
                    assists=line.stats.assists,
                    points=line.stats.points,
                    penalty_minutes=line.stats.penalty_minutes,
                    starting=line.starting,
                )
            )
        stints = [g for g in block.goalie_log if g.goalie.player_id != gs.PLACEHOLDER_PLAYER_ID]
        for sequence, stint in enumerate(stints, start=1):
            self._upsert_person(session, stint.goalie)
            session.add(
                db.GoalieAppearance(
                    game_id=game_id,
                    team_id=team_id,
                    sequence=sequence,
                    player_id=stint.goalie.player_id,
                    period_in=stint.period_in.number,
                    seconds_in=stint.seconds_in,
                    period_out=stint.period_out.number,
                    seconds_out=stint.seconds_out,
                    toi_seconds=stint.toi_seconds,
                    shots_against=stint.stats.shots_against,
                    goals_against=stint.stats.goals_against,
                    saves=stint.stats.saves,
                    result=stint.result,
                )
            )

    def _load_goal(self, session: Session, game_id: int, goal: gs.Goal) -> None:
        if goal.scorer is not None:
            self._upsert_person(session, goal.scorer)
        props = goal.properties
        session.add(
            db.GoalEvent(
                game_id=game_id,
                goal_id=goal.goal_id,
                team_id=goal.team.team_id,
                period=goal.period.number,
                seconds=goal.seconds,
                scorer_id=goal.scorer.player_id if goal.scorer else None,
                is_power_play=props.is_power_play,
                is_short_handed=props.is_short_handed,
                is_empty_net=props.is_empty_net,
                is_penalty_shot=props.is_penalty_shot,
                is_game_winning=props.is_game_winning,
            )
        )
        for sequence, assister in enumerate(goal.assists, start=1):
            self._upsert_person(session, assister)
            session.add(
                db.GoalAssist(
                    game_id=game_id,
                    goal_id=goal.goal_id,
                    sequence=sequence,
                    player_id=assister.player_id,
                )
            )

    # -- helpers -----------------------------------------------------------------

    def _upsert_person(self, session: Session, person: gs.Person) -> None:
        self._upsert_player(
            session, person.player_id, person.first_name, person.last_name, person.position
        )

    def _upsert_player(
        self, session: Session, player_id: int, first: str, last: str, position: str
    ) -> None:
        """Create players from whichever source sees them first.

        Box scores name players who are on no roster, so the roster cannot be
        the only way in. A blank position never overwrites a known one.
        """
        known = self._pending_players.get(player_id) or self._players.get(player_id)
        if known is None and (existing := session.get(db.Player, player_id)) is not None:
            known = (existing.first_name, existing.last_name, existing.position)
        if known is not None:
            position = position or known[2]
            if known == (first, last, position):
                self._pending_players[player_id] = known
                return
        session.merge(
            db.Player(player_id=player_id, first_name=first, last_name=last, position=position)
        )
        self._pending_players[player_id] = (first, last, position)

    def _dedupe(self, items: Iterable[T], key: Callable[[T], Hashable], what: str) -> list[T]:
        """Keep the first of any duplicated item, and record that it happened."""
        seen: set[Hashable] = set()
        kept: list[T] = []
        for item in items:
            k = key(item)
            if k in seen:
                self._issue("data", f"duplicate {what} {k!r}; kept the first")
                continue
            seen.add(k)
            kept.append(item)
        return kept

    def _issue(self, stage: str, message: str) -> None:
        self._issues.append(db.IngestIssue(stage=stage, message=message))

    def _finish_issues(self, resource: str, entry: ArchivedResponse) -> Iterator[db.IngestIssue]:
        game_id = _optional_param_int(entry, "game_id")
        now = datetime.now(UTC)
        for issue in self._issues:
            issue.resource = resource
            issue.game_id = game_id
            issue.created_at = now
            yield issue


def _param_int(entry: ArchivedResponse, name: str) -> int:
    value = _optional_param_int(entry, name)
    if value is None:
        raise ValueError(f"archived {entry.view} has no {name} parameter")
    return value


def _optional_param_int(entry: ArchivedResponse, name: str) -> int | None:
    value: Any = entry.params.get(name)
    return int(value) if value not in (None, "") else None


def _summarise_validation(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    more = f" (+{exc.error_count() - 1} more)" if exc.error_count() > 1 else ""
    return f"{where}: {first['msg']}{more}"
