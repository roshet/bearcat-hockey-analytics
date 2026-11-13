"""The nightly run (M7): refresh the current season, rebuild the pages, deploy.

One command, ``bca nightly``, so the scheduled task stays trivial. Each step
either completes or stops the run; nothing is deployed unless every step before
it succeeded. Each run is a row in ``pipeline_runs``:

- ``success``: rebuilt and deployed (unless ``--no-deploy``) -- every morning, even
  with no new games, so the footer's "updated" date shows the job ran today;
- ``skipped``: (older runs only) rebuilt, but not deployed because nothing changed;
- ``failed``: a step raised; ``step`` says which;
- ``canary``: the league has started recording something it never has
  (``tests/test_data_assumptions.py``). That must interrupt everything
  (docs/data-sources.md), so the run stops before export and deploy.

The outside world -- the feed, Docker, Vercel -- comes in through ``Hooks`` so
the order and the stopping rules are testable without it.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.rankings_report import store_rankings_report
from bca.analytics.scouting import store_reports
from bca.analytics.special_teams import run_special_teams
from bca.analytics.team_detail import store_detail_reports
from bca.audit.run import run_audit
from bca.config import Settings
from bca.db import schema as db
from bca.export import DEFAULT_OUT, export_static
from bca.ingest.archive import RawArchive
from bca.ingest.endpoints import CURRENT_MENS_SEASON_ID
from bca.ingest.normalize import Normalizer

logger = logging.getLogger("bca.nightly")

STEPS = ("preflight", "fetch", "normalize", "derive", "canary", "export", "deploy")
REPO = Path(__file__).parents[2]
WEB = REPO / "web"
#: How long to keep trying to start Postgres. A run caught up after sign-in starts alongside
#: Docker Desktop, whose engine can take a minute or two to accept ``docker compose``.
DB_START_SECONDS = 300
DB_START_PAUSE = 5
#: After this many days without a newer official ranking (Nov-Mar), remind.
RANKING_STALE_DAYS = 9
#: The only branch whose web code a nightly run deploys.
DEPLOY_BRANCH = "master"


@dataclass
class Hooks:
    fetch: Callable[[int], str]
    #: Store any new official ranking PDF the user downloaded; returns a line for the log.
    pick_up: Callable[[int], str]
    deploy: Callable[[], str]
    db_ready: Callable[[], bool]
    start_db: Callable[[], None]


class _Canary(Exception):
    pass


def last_success(session: Session) -> db.PipelineRun | None:
    """The latest run that rebuilt everything (deployed or found nothing to deploy)."""
    return session.scalars(
        select(db.PipelineRun).where(db.PipelineRun.status.in_(("success", "skipped")))
        .order_by(db.PipelineRun.started_at.desc()).limit(1)
    ).first()


def _last_deployed_digest(session: Session) -> str | None:
    return session.scalars(
        select(db.PipelineRun.export_digest).where(db.PipelineRun.deployed.is_(True))
        .order_by(db.PipelineRun.started_at.desc()).limit(1)
    ).first()


def ranking_reminder(session: Session, season: int, today: date) -> str | None:
    """From November to March, a nudge when no recent official ACHA ranking is stored."""
    if today.month not in (11, 12, 1, 2, 3):
        return None
    latest = session.scalar(select(func.max(db.PublishedRanking.as_of))
                            .where(db.PublishedRanking.season_id == season))
    if latest is not None and (today - latest).days <= RANKING_STALE_DAYS:
        return None
    if latest is None and today.month == 11 and today.day < 5:
        return None  # the first computer ranking comes out after Nov 4
    shown = latest.isoformat() if latest else "none yet"
    return (f"The latest official ACHA ranking stored is from {shown}; download the newest "
            f"from achahockey.org/m1-rankings into Downloads: the next morning picks it up "
            f"(or run bca rankings pickup -s {season})")


def run_nightly(sessions: sessionmaker[Session], *, archive: RawArchive, hooks: Hooks,
                out: Path = DEFAULT_OUT, season: int = CURRENT_MENS_SEASON_ID,
                deploy: bool = True, teams: list[int] | None = None,
                now: datetime | None = None) -> db.PipelineRun:
    """Run every step in order; record and return the run. Never raises for a step."""
    started = now or datetime.now(UTC)
    notes: list[str] = []

    def note(step: str, began: float, detail: str) -> None:
        line = f"{step}: {detail} ({time.monotonic() - began:.0f}s)"
        notes.append(line)
        logger.info(line)

    began = time.monotonic()
    if not hooks.db_ready():
        logger.info("preflight: database not reachable; starting it")
        hooks.start_db()
    note("preflight", began, "database ready")

    with sessions.begin() as session:
        previous = last_success(session)
        since = previous.started_at if previous else None
        deployed_digest = _last_deployed_digest(session)
        run = db.PipelineRun(started_at=started, status="running", step="preflight",
                             deployed=False, message="")
        session.add(run)
        session.flush()
        run_id = run.run_id

    status, step, digest, did_deploy = "running", "preflight", None, False
    try:
        step, began = "fetch", time.monotonic()
        note(step, began, hooks.fetch(season))
        began = time.monotonic()
        try:
            picked = hooks.pick_up(season)
        except Exception as exc:  # a bad download never stops the morning
            picked = f"rankings: pickup failed: {exc}"
        note(step, began, picked)
        if "could not" in picked or "failed" in picked:
            logger.warning(picked)

        step, began = "normalize", time.monotonic()
        tally = Normalizer(sessions, archive).run(changed_since=since)
        note(step, began, f"loaded {sum(tally.loaded.values())}, "
                          f"failed {tally.total_failed} (changed since {since or 'ever'})")

        step, began = "derive", time.monotonic()
        run_audit(sessions)
        run_special_teams(sessions)
        pages = len(store_reports(sessions, season, teams))
        store_detail_reports(sessions, season, teams)
        store_rankings_report(sessions, season)
        note(step, began, f"{pages} scouting and special-teams pages, rankings rebuilt")

        step, began = "canary", time.monotonic()
        with sessions() as session:
            canaries = session.scalar(select(func.count()).select_from(db.IngestIssue)
                                      .where(db.IngestIssue.stage == "canary")) or 0
            reminder = ranking_reminder(session, season, started.date())
        # Any canary at all, not just one new tonight: reloading a game re-creates its issues,
        # so a "new since this run" count would let the second morning deploy.
        if canaries:
            raise _Canary(f"{canaries} canary issue(s): the league is recording a field it "
                          "never has. Nothing deploys until it is investigated and resolved "
                          "(tests/test_data_assumptions.py).")
        note(step, began, "no canary issues")
        if reminder:
            notes.append(reminder)
            logger.warning(reminder)

        step, began = "export", time.monotonic()
        summary = export_static(sessions, out, now=started)
        digest = summary.digest
        note(step, began, f"{summary.files} files, data through {summary.data_through}")

        step, began = "deploy", time.monotonic()
        if not deploy:
            note(step, began, "skipped (--no-deploy)")
            status = "success"
        else:
            # Every morning, changed or not, so "updated <date>" means "checked today".
            unchanged = " (pages unchanged)" if digest == deployed_digest else ""
            note(step, began, f"deployed {hooks.deploy()}{unchanged}")
            status, did_deploy = "success", True
    except _Canary as exc:
        status = "canary"
        notes.append(str(exc))
        logger.error("CANARY: %s", exc)
    except Exception as exc:
        status = "failed"
        notes.append(f"{step} failed: {exc}")
        logger.exception("%s failed", step)

    finished = datetime.now(UTC)
    try:
        with sessions.begin() as session:
            record = session.get(db.PipelineRun, run_id)
            assert record is not None
            record.status, record.step, record.deployed = status, step, did_deploy
            record.export_digest, record.message = digest, "\n".join(notes)
            record.finished_at = finished
            session.flush()
            session.expunge(record)
        return record
    except Exception as exc:  # the database went away mid-run: report the run regardless
        notes.append(f"this run could not be recorded in pipeline_runs: {exc}")
        logger.exception("could not record run %s", run_id)
        return db.PipelineRun(run_id=run_id, started_at=started, finished_at=finished,
                              status=status, step=step, deployed=did_deploy,
                              export_digest=digest, message="\n".join(notes))


# --- the real outside world -------------------------------------------------------------


def deployment_url(stdout: str) -> str:
    """The URL ``vercel deploy`` printed: JSON when its output is piped, plain text otherwise."""
    try:
        url = json.loads(stdout)["deployment"]["url"]
        if isinstance(url, str):
            return url
    except (ValueError, KeyError, TypeError):
        pass
    urls = [line.strip() for line in stdout.splitlines() if line.strip().startswith("https://")]
    return urls[-1] if urls else "(no URL printed)"


def vercel_failure(exc: subprocess.CalledProcessError) -> str:
    """A failed Vercel CLI step, with the CLI's own last words (e.g. "not logged in")."""
    said = "\n".join(part for part in (exc.stderr, exc.output) if isinstance(part, str))
    tail = [line.strip() for line in said.splitlines() if line.strip()][-5:]
    command = " ".join(str(a) for a in exc.cmd[:2]) if isinstance(exc.cmd, list) else exc.cmd
    return f"{command} failed (exit {exc.returncode}): " + (" | ".join(tail) or "no output")


def deploy_refusal(branch: str, web_changes: str) -> str | None:
    """Why the web code on disk mustn't be deployed, or None: the build ships the working
    tree, so a morning run during feature work would publish it."""
    if branch != DEPLOY_BRANCH:
        return (f"not deploying: the checkout is on {branch!r}, not {DEPLOY_BRANCH!r} "
                "(the build would ship that branch's web code)")
    if web_changes.strip():
        return "not deploying: web/ has uncommitted changes"
    return None


def wait_for_db(start: Callable[[], None], ready: Callable[[], bool], *, limit: float,
                pause: float, clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep) -> None:
    """Ask Docker to start Postgres until it answers; a failed start is retried, not fatal."""
    deadline = clock() + limit
    while True:
        try:
            start()
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            logger.info("preflight: database not started yet (%s); retrying", exc)
        if ready():
            return
        if clock() >= deadline:
            raise RuntimeError(f"Docker Desktop isn't running, or Postgres didn't come up within "
                               f"{limit:.0f}s: start Docker Desktop and rerun bca nightly")
        sleep(pause)


def real_hooks(settings: Settings, sessions: sessionmaker[Session]) -> Hooks:
    def fetch(season: int) -> str:
        from bca.ingest.backfill import Backfill
        from bca.ingest.client import HockeyTechClient

        with HockeyTechClient(settings) as client:  # refuses without a real contact email
            results = Backfill(client).run([season], refresh_current=True)
        fetched = sum(r.fetched for r in results)
        failures = [f for r in results for f in r.failures]
        if failures and not fetched:
            raise RuntimeError(f"every request failed, e.g. {failures[0][0]}: {failures[0][1]}")
        return f"fetched {fetched}, failed {len(failures)}"

    def pick_up(season: int) -> str:
        from bca.analytics.acha_pdf import describe_pick_up, pick_up_season

        with sessions.begin() as session:
            return describe_pick_up(pick_up_season(session, season, settings))

    def db_ready() -> bool:
        try:
            with sessions() as session:
                session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def compose_up() -> None:
        subprocess.run(["docker", "compose", "up", "-d"], cwd=REPO, check=True,
                       capture_output=True, timeout=120)

    def start_db() -> None:
        wait_for_db(compose_up, db_ready, limit=DB_START_SECONDS, pause=DB_START_PAUSE)

    def deploy() -> str:
        vercel = shutil.which("vercel")
        if vercel is None:
            raise RuntimeError("the Vercel CLI isn't installed (npm i -g vercel)")

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True,
                                  text=True, timeout=60).stdout

        refusal = deploy_refusal(git("rev-parse", "--abbrev-ref", "HEAD").strip(),
                                 git("status", "--porcelain", "--", "web"))
        if refusal:
            raise RuntimeError(refusal)
        try:
            subprocess.run([vercel, "build", "--prod"], cwd=WEB, check=True,
                           capture_output=True, text=True, timeout=600)
            done = subprocess.run([vercel, "deploy", "--prebuilt", "--prod"], cwd=WEB,
                                  check=True, capture_output=True, text=True, timeout=600)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(vercel_failure(exc)) from exc
        return deployment_url(done.stdout)

    return Hooks(fetch=fetch, pick_up=pick_up, deploy=deploy, db_ready=db_ready,
                 start_db=start_db)


def log_to_file(day: date, root: Path = REPO / "data" / "logs") -> Path:
    """Send this run's log to ``data/logs/nightly-YYYY-MM-DD.log`` (appending)."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"nightly-{day.isoformat()}.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return path
