"""The nightly pipeline (M7), with the network, Docker and Vercel stubbed."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from bca.db import schema as db
from bca.ingest import endpoints
from bca.ingest.archive import RawArchive
from bca.nightly import Hooks, last_success, ranking_reminder, run_nightly
from tests.unit.test_normalize import UC, archive_payload, build_archive, payload

KENTUCKY = 346


class Stubs:
    """Records what the pipeline asked the outside world to do."""

    def __init__(self, archive: RawArchive) -> None:
        self.archive = archive
        self.deploys = 0
        self.fetches = 0
        self.ready = [True]
        self.started_db = 0
        self.fail_fetch = False
        self.canary = False
        self.picked = "rankings: nothing new in the inbox"
        self.sessions: sessionmaker[Session] | None = None

    def fetch(self, season: int) -> str:
        self.fetches += 1
        if self.fail_fetch:
            raise RuntimeError("feed unreachable")
        if self.canary and self.sessions is not None:
            with self.sessions.begin() as s:
                s.add(db.IngestIssue(resource="x", game_id=None, stage="canary",
                                     message="shots recorded", created_at=datetime.now(UTC)))
        return "fetched 0"

    def pick_up(self, season: int) -> str:
        if self.picked == "raise":
            raise OSError("Downloads unreadable")
        return self.picked

    def deploy(self) -> str:
        self.deploys += 1
        return "https://bearcat.example"

    def db_ready(self) -> bool:
        return self.ready.pop(0) if len(self.ready) > 1 else self.ready[0]

    def start_db(self) -> None:
        self.started_db += 1

    def hooks(self) -> Hooks:
        return Hooks(fetch=self.fetch, pick_up=self.pick_up, deploy=self.deploy,
                     db_ready=self.db_ready, start_db=self.start_db)


@pytest.fixture
def world(tmp_path, sessions: sessionmaker[Session]):
    archive = build_archive(tmp_path / "cache")
    archive_payload(archive, endpoints.game_summary(33157), payload("game_summary_33157"))
    stubs = Stubs(archive)
    stubs.sessions = sessions
    return sessions, archive, stubs, tmp_path / "data"


def run(world, **kwargs):
    sessions, archive, stubs, out = world
    return run_nightly(sessions, archive=archive, out=out, hooks=stubs.hooks(),
                       teams=[KENTUCKY, UC], **kwargs)


def test_a_clean_run_records_success_and_deploys_once(world):
    result = run(world)
    assert (result.status, result.deployed, result.step) == ("success", True, "deploy")
    assert world[2].deploys == 1
    assert result.export_digest and (world[3] / "index.json").exists()


def test_an_unchanged_morning_still_deploys(world):
    # So the footer's "updated" date always means "checked this morning": an old date then
    # reliably means the job isn't running.
    first = run(world)
    second = run(world)
    assert (second.status, second.deployed) == ("success", True)
    assert second.export_digest == first.export_digest
    assert world[2].deploys == 2


def test_no_deploy_flag_never_deploys(world):
    result = run(world, deploy=False)
    assert (result.status, result.deployed) == ("success", False)
    assert world[2].deploys == 0


def test_a_failing_step_records_failed_and_never_deploys(world):
    world[2].fail_fetch = True
    result = run(world)
    assert (result.status, result.step, result.deployed) == ("failed", "fetch", False)
    assert "feed unreachable" in result.message
    assert world[2].deploys == 0


def test_changed_since_is_the_last_successful_runs_start(world, monkeypatch):
    from bca.ingest import normalize

    seen: list[datetime | None] = []
    real = normalize.Normalizer.run

    def spy(self, views=None, changed_since=None):  # type: ignore[no-untyped-def]
        seen.append(changed_since)
        return real(self, views, changed_since=changed_since)

    monkeypatch.setattr(normalize.Normalizer, "run", spy)
    first = run(world, now=datetime(2026, 10, 24, 7, tzinfo=UTC))
    world[2].fail_fetch = True
    run(world, now=datetime(2026, 10, 25, 7, tzinfo=UTC))
    world[2].fail_fetch = False
    run(world, now=datetime(2026, 10, 26, 7, tzinfo=UTC))
    naive = first.started_at.replace(tzinfo=None)
    assert seen[0] is None  # the very first run loads everything
    # The failed second run never reached normalize; the third starts from the first's start.
    assert len(seen) == 2 and seen[1] is not None and seen[1].replace(tzinfo=None) == naive
    with world[0]() as session:
        assert last_success(session) is not None


def test_the_ranking_pickup_is_in_the_run_notes(world):
    world[2].picked = "rankings: stored 2627computer-average-2026-11-15.pdf (as of 2026-11-15)"
    result = run(world)
    assert result.status == "success"
    assert "stored 2627computer-average-2026-11-15.pdf" in result.message


@pytest.mark.parametrize("picked", ["rankings: could not store x.pdf: no 'as of' date found",
                                    "raise"])
def test_a_bad_ranking_download_never_stops_the_morning(world, picked):
    world[2].picked = picked
    result = run(world)
    assert (result.status, result.deployed) == ("success", True)
    expected = "Downloads unreadable" if picked == "raise" else "could not store x.pdf"
    assert expected in result.message


def test_a_new_canary_issue_stops_before_deploy(world):
    world[2].canary = True
    result = run(world)
    assert (result.status, result.step, result.deployed) == ("canary", "canary", False)
    assert world[2].deploys == 0
    assert not (world[3] / "index.json").exists()


def test_the_database_is_started_when_not_ready(world):
    world[2].ready = [False, True]
    result = run(world)
    assert world[2].started_db == 1 and result.status == "success"


def test_ranking_reminder_window(sessions):
    def stored(as_of: date) -> None:
        with sessions.begin() as s:
            s.add(db.PublishedRanking(season_id=73, as_of=as_of, team_id=UC, rank=1, rank_a=1,
                                      rank_b=1, avg=1.0, league="X", source_file="f.pdf"))

    with sessions() as s:
        assert ranking_reminder(s, 73, date(2026, 10, 30)) is None
        assert "bca rankings pickup -s 73" in (ranking_reminder(s, 73, date(2026, 11, 10)) or "")
    stored(date(2026, 11, 4))
    with sessions() as s:
        assert ranking_reminder(s, 73, date(2026, 11, 10)) is None
        assert "2026-11-04" in (ranking_reminder(s, 73, date(2026, 11, 20)) or "")
        assert ranking_reminder(s, 73, date(2027, 4, 2)) is None


def test_deployment_url_reads_the_clis_json_or_its_plain_text():
    from bca.nightly import deployment_url

    as_json = '{\n  "status": "ok",\n  "deployment": {"url": "https://x-abc.vercel.app"}\n}'
    assert deployment_url(as_json) == "https://x-abc.vercel.app"
    assert deployment_url("Inspect: ...\nhttps://x-def.vercel.app\n") == "https://x-def.vercel.app"
    assert deployment_url("") == "(no URL printed)"


class FakeClock:
    """Time that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_wait_for_db_retries_while_docker_is_still_starting():
    from bca.nightly import wait_for_db

    clock, starts = FakeClock(), []

    def start() -> None:
        starts.append(clock.now)
        if len(starts) <= 2:
            raise RuntimeError("Docker engine not ready")

    wait_for_db(start, lambda: len(starts) >= 3, limit=300, pause=5, clock=clock, sleep=clock.sleep)
    assert starts == [0.0, 5.0, 10.0]


def test_wait_for_db_gives_up_with_a_clear_message():
    from bca.nightly import wait_for_db

    clock = FakeClock()

    def start() -> None:
        raise RuntimeError("Docker engine not ready")

    with pytest.raises(RuntimeError, match="Docker Desktop isn't running"):
        wait_for_db(start, lambda: False, limit=300, pause=5, clock=clock, sleep=clock.sleep)
    assert 295 <= clock.now <= 305


def test_wait_for_db_starts_once_when_the_database_answers():
    from bca.nightly import wait_for_db

    clock, starts = FakeClock(), []
    wait_for_db(lambda: starts.append(1), lambda: True, limit=300, pause=5,
                clock=clock, sleep=clock.sleep)
    assert starts == [1] and clock.now == 0


def test_a_canary_keeps_blocking_deploys_until_it_is_resolved(world):
    world[2].canary = True
    run(world)
    world[2].canary = False  # the finding is still in ingest_issues: the next morning must stop too
    second = run(world)
    assert (second.status, second.deployed) == ("canary", False)
    assert world[2].deploys == 0


def test_deploys_only_from_a_clean_main():
    from bca.nightly import deploy_refusal

    assert deploy_refusal("master", "") is None  # this repo's main branch
    assert "m8-video" in (deploy_refusal("m8-video", "") or "")
    assert "uncommitted" in (deploy_refusal("master", " M web/src/App.tsx\n") or "")


def test_a_failed_vercel_step_keeps_the_clis_reason():
    import subprocess

    from bca.nightly import vercel_failure

    exc = subprocess.CalledProcessError(1, ["vercel", "deploy"], output="Inspect: x\n",
                                        stderr="Vercel CLI 54.5\nError: You are not logged in.\n")
    message = vercel_failure(exc)
    assert "vercel deploy" in message and "You are not logged in." in message


def test_a_run_that_cannot_be_recorded_still_reports(world):
    sessions, archive, stubs, out = world

    class Gone:
        """The database disappears once the deploy is done."""

        def __call__(self):  # type: ignore[no-untyped-def]
            return sessions()

        def begin(self):  # type: ignore[no-untyped-def]
            if stubs.deploys:
                raise OSError("connection refused")
            return sessions.begin()

    result = run_nightly(Gone(), archive=archive, out=out, hooks=stubs.hooks(),  # type: ignore[arg-type]
                         teams=[KENTUCKY, UC])
    assert result.deployed and "could not be recorded" in result.message
