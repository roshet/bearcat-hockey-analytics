"""Normalizer tests: live fixtures -> archive -> migrated SQLite database.

Building the database through the Alembic migration (not ``create_all``) means
these tests also prove the migration matches the models.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from bca.db import schema as db
from bca.ingest import endpoints
from bca.ingest.archive import RawArchive
from bca.ingest.client import FeedRequest
from bca.ingest.normalize import Normalizer
from tests.fixture_data import fixture_json

UC = 265


def payload(name: str) -> Any:
    return fixture_json(f"live/{name}.json")


def archive_payload(archive: RawArchive, request: FeedRequest, data: Any) -> None:
    archive.write(
        request.cache_key,
        identity=request.identity,
        feed=request.feed,
        params=request.params,
        fetched_at=datetime(2026, 10, 21, tzinfo=UTC),
        payload=data,
        raw_text=json.dumps(data),
    )


@pytest.fixture
def archive(tmp_path) -> RawArchive:
    return build_archive(tmp_path / "cache")


def build_archive(root: Path) -> RawArchive:
    """UC's 2026-27 reference data, the 33169 box score and UC's upstream season lines."""
    archive = RawArchive(root)
    archive_payload(archive, endpoints.seasons(), payload("seasons"))
    archive_payload(archive, endpoints.teams_by_season(73), payload("teams_by_season"))
    archive_payload(archive, endpoints.team_schedule(UC, 73), payload("team_schedule"))
    archive_payload(archive, endpoints.team_roster(UC, 73), payload("team_roster"))
    archive_payload(archive, endpoints.game_summary(33169), payload("game_summary"))
    archive_payload(archive, endpoints.skater_stats(73, UC), payload("skater_stats"))
    archive_payload(archive, endpoints.goalie_stats(73, UC), payload("goalie_stats"))
    return archive


def count(sessions: sessionmaker[Session], model: type[db.Base]) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_loads_everything_without_issues(archive, sessions):
    tally = Normalizer(sessions, archive).run()

    assert tally.total_failed == 0
    assert dict(tally.issues) == {}
    assert tally.loaded == {
        "seasons": 1,
        "teamsbyseason": 1,
        "schedule": 1,
        "roster": 1,
        "gameSummary": 1,
        "players": 2,
    }


def test_upstream_season_lines_load_as_comparison_data(archive, sessions):
    Normalizer(sessions, archive).run()

    with sessions() as session:
        goalies = session.scalars(
            select(db.UpstreamPlayerSeasonStats).where(
                db.UpstreamPlayerSeasonStats.kind == "goalie"
            )
        ).all()
        upstream_only = session.get(db.Player, 36407)
    assert sum(g.goals_against or 0 for g in goalies) == 76
    assert {g.player_id for g in goalies} >= {36407}
    assert upstream_only is None, "an upstream line never creates a player"
    assert count(sessions, db.UpstreamPlayerSeasonStats) == 29 + 5


def test_a_full_page_of_upstream_lines_is_refused(archive, sessions):
    full = endpoints.goalie_stats(73, UC, limit=5)
    archive_payload(archive, full, payload("goalie_stats"))

    tally = Normalizer(sessions, archive).run()

    assert tally.failed["players"] == 1
    with sessions() as session:
        issue = session.scalars(
            select(db.IngestIssue).where(db.IngestIssue.stage == "load")
        ).one()
    assert "may be truncated" in issue.message


def test_player_stats_request_the_season_by_its_working_parameter():
    """``season_id`` is silently ignored by this view (it answers with the current
    season), so the request must carry ``season``."""
    params = endpoints.goalie_stats(21, 7).params
    assert params["season"] == "21"
    assert "season_id" not in params


def test_an_upstream_entry_requested_by_season_id_is_refused(archive, sessions):
    stale = endpoints.goalie_stats(73, UC)
    stale = FeedRequest(stale.feed, {**{k: v for k, v in stale.params.items() if k != "season"},
                                     "season_id": "21"})
    archive_payload(archive, stale, payload("goalie_stats"))

    tally = Normalizer(sessions, archive).run()

    assert tally.failed["players"] == 1
    with sessions() as session:
        seasons = set(session.scalars(select(db.UpstreamPlayerSeasonStats.season_id)))
    assert seasons == {73}, "the stale entry did not load as season 21"


def test_a_single_view_can_be_reloaded_alone(archive, sessions):
    Normalizer(sessions, archive).run()

    tally = Normalizer(sessions, archive).run(["players"])

    assert dict(tally.loaded) == {"players": 2}
    assert count(sessions, db.GoalEvent) == 9, "box scores untouched"


def test_an_unknown_view_is_refused(archive, sessions):
    with pytest.raises(ValueError, match="unknown views"):
        Normalizer(sessions, archive).run(["standings"])


def test_box_score_kind_is_recorded(archive, sessions):
    Normalizer(sessions, archive).run()
    with sessions() as session:
        game = session.get(db.Game, 33169)
    assert game is not None and game.box_score_kind == "full"


def test_uc_record_rebuilt_from_game_rows(archive, sessions):
    Normalizer(sessions, archive).run()

    with sessions() as session:
        games = session.scalars(select(db.Game).where(db.Game.is_final)).all()

    gf = sum(g.home_goals if g.home_team_id == UC else g.visiting_goals for g in games)
    ga = sum(g.visiting_goals if g.home_team_id == UC else g.home_goals for g in games)
    wins = sum(
        (g.home_goals > g.visiting_goals) == (g.home_team_id == UC) for g in games
    )
    assert (wins, len(games) - wins, gf, ga) == (2, 8, 25, 63)


def test_reference_game_33169(archive, sessions):
    Normalizer(sessions, archive).run()

    with sessions() as session:
        game = session.get(db.Game, 33169)
        assert game is not None and game.summary_normalized
        assert (game.home_goals, game.visiting_goals) == (7, 2)
        assert game.attendance is None, "0 means not entered"

        shots = dict(
            session.execute(
                select(db.GameTeamStats.team_id, db.GameTeamStats.shots).where(
                    db.GameTeamStats.game_id == 33169
                )
            ).all()
        )
        assert shots == {265: 35, 386: 24}

        uc_goalies = session.execute(
            select(db.Player.player_id)
            .join(db.GoalieAppearance, db.GoalieAppearance.player_id == db.Player.player_id)
            .where(db.GoalieAppearance.game_id == 33169, db.GoalieAppearance.team_id == UC)
        ).scalars().all()
        assert uc_goalies == [33425]

        pim = session.scalar(
            select(func.sum(db.PenaltyEvent.minutes)).where(db.PenaltyEvent.game_id == 33169)
        )
        assert pim == 111

    assert count(sessions, db.GoalEvent) == 9
    assert count(sessions, db.PeriodStats) == 3


def test_reference_data(archive, sessions):
    Normalizer(sessions, archive).run()

    with sessions() as session:
        md1 = session.scalar(
            select(func.count()).select_from(db.TeamSeason).where(db.TeamSeason.is_md1)
        )
        uc = session.get(db.TeamSeason, (UC, 73))
        coach = session.scalar(
            select(db.StaffEntry.person_id).where(db.StaffEntry.role == "Head Coach")
        )

    assert md1 == 81
    assert uc is not None and uc.division_name == "MD1 SECHL"
    assert coach == 37954
    assert count(sessions, db.RosterEntry) == 26


def test_renormalizing_is_idempotent(archive, sessions):
    tables = [db.Game, db.Player, db.GoalEvent, db.GoalAssist, db.PenaltyEvent,
              db.SkaterGameStats, db.RosterEntry, db.IngestIssue]

    Normalizer(sessions, archive).run()
    first = {t.__tablename__: count(sessions, t) for t in tables}
    Normalizer(sessions, archive).run()
    second = {t.__tablename__: count(sessions, t) for t in tables}

    assert first == second


def test_a_broken_box_score_is_recorded_not_fatal(archive, sessions):
    broken = copy.deepcopy(payload("game_summary"))
    broken["periods"][0]["goals"][0]["time"] = "not a clock"
    archive_payload(archive, endpoints.game_summary(33169), broken)

    tally = Normalizer(sessions, archive).run()

    assert tally.failed["gameSummary"] == 1
    assert tally.loaded["schedule"] == 1, "the rest of the run carried on"
    with sessions() as session:
        issue = session.scalars(select(db.IngestIssue)).one()
        game = session.get(db.Game, 33169)
    assert issue.stage == "parse"
    assert issue.game_id == 33169
    assert "clock" in issue.message
    assert game is not None and not game.summary_normalized
    assert count(sessions, db.GoalEvent) == 0, "a failed game leaves no partial rows"


def test_fixing_the_archive_clears_the_issue(archive, sessions):
    broken = copy.deepcopy(payload("game_summary"))
    broken["periods"][0]["goals"][0]["time"] = "not a clock"
    archive_payload(archive, endpoints.game_summary(33169), broken)
    Normalizer(sessions, archive).run()

    archive_payload(archive, endpoints.game_summary(33169), payload("game_summary"))
    Normalizer(sessions, archive).run()

    assert count(sessions, db.IngestIssue) == 0
    assert count(sessions, db.GoalEvent) == 9


def test_box_score_without_a_scheduled_game_is_an_issue(tmp_path, sessions):
    archive = RawArchive(tmp_path / "lonely")
    archive_payload(archive, endpoints.game_summary(33169), payload("game_summary"))

    tally = Normalizer(sessions, archive).run()

    assert tally.failed["gameSummary"] == 1
    with sessions() as session:
        issue = session.scalars(select(db.IngestIssue)).one()
    assert issue.stage == "load"
    assert "no archived schedule" in issue.message


def test_canary_findings_become_issues(archive, sessions):
    changed = copy.deepcopy(payload("game_summary"))
    changed["homeTeam"]["skaters"][0]["stats"]["shots"] = 4
    archive_payload(archive, endpoints.game_summary(33169), changed)

    tally = Normalizer(sessions, archive).run()

    assert tally.issues["canary"] == 1
    assert tally.failed["gameSummary"] == 0, "a canary does not stop the game loading"


TRIAL_GAME = 12493  # one of the September 2023 trial games


def archive_trial_game(archive: RawArchive, stat: str) -> None:
    """33169, re-labelled as a trial game in schedule and box score, with one skater
    stat entered."""
    schedule = copy.deepcopy(payload("team_schedule"))
    row = schedule["SiteKit"]["Schedule"][0]
    row.update(id=str(TRIAL_GAME), game_id=str(TRIAL_GAME), season_id="34")
    schedule["SiteKit"]["Schedule"] = [row]
    archive_payload(archive, endpoints.team_schedule(UC, 34), schedule)

    raw = copy.deepcopy(payload("game_summary"))
    raw["details"]["id"] = TRIAL_GAME
    raw["homeTeam"]["skaters"][0]["stats"][stat] = 2
    archive_payload(archive, endpoints.game_summary(TRIAL_GAME), raw)


def test_september_2023_trial_fields_are_data_not_canary(archive, sessions):
    archive_trial_game(archive, "shots")

    tally = Normalizer(sessions, archive).run()

    assert tally.issues["canary"] == 0
    with sessions() as session:
        messages = session.scalars(
            select(db.IngestIssue.message).where(db.IngestIssue.game_id == TRIAL_GAME)
        ).all()
    assert any("September 2023 trial" in m for m in messages)


def test_a_new_field_in_a_trial_game_is_still_a_canary(archive, sessions):
    archive_trial_game(archive, "faceoffWins")

    tally = Normalizer(sessions, archive).run()

    assert tally.issues["canary"] == 1


def test_canary_is_counted_even_when_the_game_cannot_load(archive, sessions):
    changed = copy.deepcopy(payload("game_summary"))
    changed["homeTeam"]["skaters"][0]["stats"]["hits"] = 1
    archive_payload(archive, endpoints.game_summary(99999), changed)  # wrong game id

    tally = Normalizer(sessions, archive).run()

    assert tally.failed["gameSummary"] == 1
    assert tally.issues["canary"] == 1


def test_duplicate_penalty_is_kept_once_and_reported(archive, sessions):
    doubled = copy.deepcopy(payload("game_summary"))
    first = doubled["periods"][0]["penalties"][0]
    doubled["periods"][0]["penalties"].append(copy.deepcopy(first))
    archive_payload(archive, endpoints.game_summary(33169), doubled)

    tally = Normalizer(sessions, archive).run()

    assert tally.issues["data"] == 1
    assert count(sessions, db.PenaltyEvent) == 15


def test_game_with_no_goal_times_loads_and_is_flagged(archive, sessions):
    """Real pattern: some scorekeepers leave every goal time blank."""
    untimed = copy.deepcopy(payload("game_summary"))
    for period in untimed["periods"]:
        for goal in period["goals"]:
            goal["time"] = ""
    archive_payload(archive, endpoints.game_summary(33169), untimed)

    tally = Normalizer(sessions, archive).run()

    assert tally.failed["gameSummary"] == 0
    with sessions() as session:
        issue = session.scalars(select(db.IngestIssue)).one()
        seconds = session.scalars(select(db.GoalEvent.seconds)).all()
    assert issue.stage == "data"
    assert issue.message == "9 of 9 goals have no time entered"
    assert seconds == [None] * 9


def test_migration_matches_the_models(sessions):
    """The migration and schema.py must never drift apart."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = sessions.kw["bind"]
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), db.Base.metadata)
    assert diff == []


def score_only(raw: Any) -> Any:
    """Reshape a real box score into the score-only form seen in live data."""
    placeholder = {"id": 0, "firstName": None, "lastName": None, "jerseyNumber": 0,
                   "position": None}
    blank = {"goals": 0, "assists": 0, "points": 0, "penaltyMinutes": 0}
    for side in ("homeTeam", "visitingTeam"):
        raw[side]["skaters"] = [{"info": placeholder, "stats": blank, "starting": 0}]
        raw[side]["goalieLog"] = []
        raw[side]["stats"]["shots"] = 0
    for period in raw["periods"]:
        period["penalties"] = []
        period["stats"]["homeShots"] = period["stats"]["visitingShots"] = "0"
        for goal in period["goals"]:
            goal.update(time="", scoredBy=placeholder, assists=[])
    return raw


def test_score_only_box_score_keeps_goals_but_invents_no_players(archive, sessions):
    archive_payload(
        archive, endpoints.game_summary(33169), score_only(copy.deepcopy(payload("game_summary")))
    )

    tally = Normalizer(sessions, archive).run()

    assert tally.failed["gameSummary"] == 0
    with sessions() as session:
        goals = session.execute(select(db.GoalEvent.team_id, db.GoalEvent.scorer_id)).all()
        issue = session.scalars(select(db.IngestIssue)).one()
        assert session.get(db.Player, 0) is None, "no phantom player 0"
    assert sorted(t for t, _ in goals) == [265] * 7 + [386] * 2
    assert {s for _, s in goals} == {None}
    assert count(sessions, db.SkaterGameStats) == 0
    assert count(sessions, db.GoalieAppearance) == 0
    assert issue.stage == "data" and issue.message.startswith("score-only")
    with sessions() as session:
        game = session.get(db.Game, 33169)
    assert game is not None and game.box_score_kind == "score_only"


# --- incremental normalize (M7) ---------------------------------------------------------


def _age(archive: RawArchive, request: FeedRequest, when: datetime) -> None:
    stamp = when.timestamp()
    os.utime(archive.path_for(request.cache_key), (stamp, stamp))


def test_changed_since_skips_entries_written_before_it(tmp_path, sessions):
    archive = build_archive(tmp_path / "cache")
    archive_payload(archive, endpoints.game_summary(33157), payload("game_summary_33157"))
    old = datetime(2026, 10, 1, tzinfo=UTC)
    for path in archive.root.glob("*.json"):  # everything archived "long ago"...
        os.utime(path, (old.timestamp(), old.timestamp()))
    Normalizer(sessions, archive).run()  # ...and already loaded once
    archive_payload(archive, endpoints.game_summary(33171), payload("game_summary_33171"))

    tally = Normalizer(sessions, archive).run(changed_since=datetime(2026, 10, 20, tzinfo=UTC))
    assert tally.loaded["gameSummary"] == 1  # only the new box score
    assert tally.loaded["schedule"] == 0 and tally.loaded["seasons"] == 0


def test_a_rewritten_entry_is_reloaded(tmp_path, sessions):
    archive = build_archive(tmp_path / "cache")
    _age(archive, endpoints.team_schedule(UC, 73), datetime(2026, 10, 1, tzinfo=UTC))
    tally = Normalizer(sessions, archive).run(changed_since=datetime(2026, 10, 20, tzinfo=UTC))
    assert tally.loaded["schedule"] == 0
    archive_payload(archive, endpoints.team_schedule(UC, 73), payload("team_schedule"))
    tally = Normalizer(sessions, archive).run(changed_since=datetime(2026, 10, 20, tzinfo=UTC))
    assert tally.loaded["schedule"] == 1


def test_without_changed_since_everything_loads(tmp_path, sessions):
    archive = build_archive(tmp_path / "cache")
    for path in archive.root.glob("*.json"):
        os.utime(path, (0, 0))
    tally = Normalizer(sessions, archive).run()
    assert tally.loaded["gameSummary"] == 1 and tally.loaded["schedule"] == 1
