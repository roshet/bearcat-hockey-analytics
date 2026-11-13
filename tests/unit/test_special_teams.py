"""The special-teams run end to end: archive -> normalize -> audit -> simulate -> tables."""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.special_teams import load, run_special_teams
from bca.audit.run import run_audit
from bca.db import schema as db
from bca.ingest import endpoints
from bca.ingest.archive import RawArchive
from bca.ingest.normalize import Normalizer
from tests.unit.test_normalize import UC, archive_payload, build_archive, payload, score_only


@pytest.fixture
def archive(tmp_path) -> RawArchive:
    archive = build_archive(tmp_path / "cache")
    for game_id in (32130, 31980):
        archive_payload(archive, endpoints.game_summary(game_id),
                        payload(f"game_summary_{game_id}"))
    return archive


def prepared(sessions: sessionmaker[Session], archive: RawArchive) -> None:
    Normalizer(sessions, archive).run()
    run_audit(sessions)  # game_quality decides which games are simulable


def count(sessions: sessionmaker[Session], model: type[db.Base]) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_32130_is_stored_with_the_entered_count_beside_the_derived_one(archive, sessions):
    prepared(sessions, archive)
    run_special_teams(sessions)

    with sessions() as session:
        uc = session.get(db.TeamGameSpecialTeams, (32130, UC))
        home = session.get(db.TeamGameSpecialTeams, (32130, 5))
    assert uc is not None and home is not None
    assert (home.pp_opportunities, uc.pp_opportunities) == (3, 7)
    assert (home.entered_pp_opportunities, uc.entered_pp_opportunities) == (5, 5)
    assert (home.pp_seconds, uc.pk_seconds) == (257, 257)


def test_every_goal_and_penalty_gets_a_row(archive, sessions):
    prepared(sessions, archive)
    run_special_teams(sessions)

    with sessions() as session:
        goals = session.scalar(select(func.count()).select_from(db.GoalEvent).where(
            db.GoalEvent.game_id.in_((33169, 32130, 31980))))
        penalties = session.scalar(select(func.count()).select_from(db.PenaltyEvent).where(
            db.PenaltyEvent.game_id.in_((33169, 32130, 31980))))
        misconduct = session.scalars(
            select(db.PenaltyOutcome).join(
                db.PenaltyEvent, db.PenaltyEvent.penalty_id == db.PenaltyOutcome.penalty_id
            ).where(db.PenaltyEvent.game_id == 32130, db.PenaltyEvent.minutes == 10)
        ).one()
    assert count(sessions, db.GoalStrength) == goals
    assert count(sessions, db.PenaltyOutcome) == penalties
    assert misconduct.flagged_power_play and misconduct.advantage_seconds == 0, (
        "the scorekeeper flagged a game misconduct; it can never give an advantage"
    )


def test_pp_goals_against_store_the_penalty_being_served(archive, sessions):
    prepared(sessions, archive)
    run_special_teams(sessions)

    with sessions() as session:
        rows = session.execute(
            select(db.GoalStrengthPenalty.goal_id, db.PenaltyEvent.description)
            .join(db.PenaltyEvent, db.PenaltyEvent.penalty_id == db.GoalStrengthPenalty.penalty_id)
            .where(db.GoalStrengthPenalty.game_id == 32130)
        ).all()
        pp_goals = session.scalar(select(func.count()).select_from(db.GoalStrength).where(
            db.GoalStrength.game_id == 32130, db.GoalStrength.situation == "pp"))
    assert len(rows) == pp_goals == 3
    assert {d for _, d in rows} == {"Major-Cross Checking"}


def test_unenterable_games_are_skipped_and_counted(archive, sessions):
    archive_payload(archive, endpoints.game_summary(33169),
                    score_only(copy.deepcopy(payload("game_summary"))))
    prepared(sessions, archive)

    with sessions() as session:
        league = load(session)
    assert league.skipped["score-only box score"] == 1
    assert 33169 not in {g.game_id for g in league.games}


def test_special_teams_run_is_idempotent(archive, sessions):
    prepared(sessions, archive)
    run_special_teams(sessions)
    first = [count(sessions, t) for t in (db.TeamGameSpecialTeams, db.GoalStrength,
                                          db.PenaltyOutcome)]
    run_special_teams(sessions)
    second = [count(sessions, t) for t in (db.TeamGameSpecialTeams, db.GoalStrength,
                                           db.PenaltyOutcome)]
    assert first == second and all(first)
