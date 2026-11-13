"""M2 audit tests: real box scores -> archive -> migrated SQLite -> reconcile + completeness.

Golden cases are the three the M2 exit criterion names: UC's 2026-27 goals
against (63 in the games, 76 upstream), the goalie no box score mentions
(player 36407), and game 33170's power-play goal on zero opportunities.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from bca.audit.report import build_report
from bca.audit.run import run_audit
from bca.db import schema as db
from bca.ingest import endpoints
from bca.ingest.archive import RawArchive
from bca.ingest.normalize import Normalizer
from tests.unit.test_normalize import UC, archive_payload, build_archive, payload

KENT_STATE = 386


@pytest.fixture
def archive(tmp_path) -> RawArchive:
    """test_normalize's archive plus 33170 (PP goal on 0 opps) and 31980 (empty net)."""
    archive = build_archive(tmp_path / "cache")
    archive_payload(archive, endpoints.game_summary(33170), payload("game_summary_33170"))
    archive_payload(archive, endpoints.game_summary(31980), payload("game_summary_31980"))
    return archive


def audited(sessions: sessionmaker[Session], archive: RawArchive) -> None:
    Normalizer(sessions, archive).run()
    run_audit(sessions)


def findings(sessions: sessionmaker[Session], **where: Any) -> list[db.ReconciliationFinding]:
    f = db.ReconciliationFinding
    query = select(f)
    for column, value in where.items():
        query = query.where(getattr(f, column) == value)
    with sessions() as session:
        return list(session.scalars(query.order_by(f.finding_id)))


# --- the named cases --------------------------------------------------------------


def test_33170_power_play_goal_on_zero_opportunities(archive, sessions):
    audited(sessions, archive)

    [finding] = findings(sessions, check_name="pp_goals_within_opportunities", game_id=33170)
    assert (finding.team_id, finding.expected, finding.actual) == (KENT_STATE, 0, 1)
    assert finding.explanation == "unentered_zero", "UC took minors; the 0 was never entered"


def test_31980_goalie_shortfall_is_the_empty_net_goal(archive, sessions):
    audited(sessions, archive)

    [finding] = findings(sessions, check_name="goalie_ga_matches", game_id=31980)
    assert (finding.team_id, finding.expected, finding.actual) == (UC, 5, 4)
    assert finding.explanation == "empty_net"


def test_batchler_is_credited_upstream_but_in_no_box_score(archive, sessions):
    audited(sessions, archive)

    batchler = findings(sessions, check_name="upstream_goalie_ga", player_id=36407)
    assert [(f.expected, f.actual, f.explanation) for f in batchler] == [
        (0, 14, "upstream_only_player")
    ]


def test_named_cases_report_uc_goals_against(archive, sessions):
    audited(sessions, archive)
    with sessions() as session:
        cases = build_report(session).named_cases

    ga = cases["uc_2026_27_goals_against"]
    assert ga["game_rows"] == 63
    assert ga["upstream_goalie_lines"] == 76
    # Only 3 of UC's 10 box scores are fixtures, so a goalie who played in two others
    # also looks upstream-only here; in the full data only 36407 does.
    only = {g["player_id"]: g["goals_against"] for g in ga["upstream_only_goalies"]}
    assert only[36407] == 14
    assert cases["game_31980_goalie_ga"]["explanation"] == "empty_net"


# --- the reference game is clean -----------------------------------------------------


def test_33169_fails_only_on_its_unentered_power_play_opportunities(archive, sessions):
    audited(sessions, archive)

    game_findings = {(f.check_name, f.team_id) for f in findings(sessions, game_id=33169)}
    assert game_findings == {("pp_goals_within_opportunities", UC)}


# --- each check catches a constructed fault ------------------------------------------------


def _home_goals(raw: Any) -> None:
    raw["homeTeam"]["stats"]["goals"] = 6


def _saves(raw: Any) -> None:
    raw["homeTeam"]["goalieLog"][0]["stats"]["saves"] = 1


def _period_shots(raw: Any) -> None:
    raw["periods"][0]["stats"]["homeShots"] = "99"


def _skater_goals(raw: Any) -> None:
    raw["homeTeam"]["skaters"][0]["stats"]["goals"] += 3


def _team_pim(raw: Any) -> None:
    raw["homeTeam"]["stats"]["penaltyMinuteCount"] = 1


def _no_goalies(raw: Any) -> None:
    raw["homeTeam"]["goalieLog"] = []


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (_home_goals, "score_matches_box"),
        (_saves, "goalie_saves"),
        (_period_shots, "period_shots_sum"),
        (_skater_goals, "skater_goals_match"),
        (_team_pim, "pim_matches"),
        (_no_goalies, "goalies_recorded"),
    ],
)
def test_each_check_catches_its_fault(
    archive, sessions, mutate: Callable[[Any], None], check: str
):
    raw = copy.deepcopy(payload("game_summary"))
    mutate(raw)
    archive_payload(archive, endpoints.game_summary(33169), raw)

    audited(sessions, archive)

    caught = findings(sessions, check_name=check, game_id=33169)
    assert caught, f"{check} did not catch the fault"
    assert all(f.explanation == "unexplained" for f in caught)


def test_real_box_score_players_missing_from_uc_roster(archive, sessions):
    """Real: two players in these UC box scores are on no UC roster."""
    audited(sessions, archive)

    missing = findings(sessions, check_name="player_on_roster", team_id=UC)
    assert [f.player_id for f in missing] == [22681, 44680]


def test_box_score_player_missing_from_roster(archive, sessions):
    roster = copy.deepcopy(payload("team_roster"))
    starter = next(p for p in roster["SiteKit"]["Roster"]
                   if isinstance(p, dict) and str(p.get("player_id")) == "33425")
    roster["SiteKit"]["Roster"].remove(starter)
    archive_payload(archive, endpoints.team_roster(UC, 73), roster)

    audited(sessions, archive)

    missing = findings(sessions, check_name="player_on_roster", team_id=UC)
    assert [f.player_id for f in missing] == [22681, 33425, 44680]


# --- bookkeeping -------------------------------------------------------------------------


def test_score_only_games_are_not_checkable_rather_than_passing(archive, sessions):
    from tests.unit.test_normalize import score_only

    archive_payload(
        archive, endpoints.game_summary(33169), score_only(copy.deepcopy(payload("game_summary")))
    )
    audited(sessions, archive)

    with sessions() as session:
        total = session.get(db.AuditCheckTotal, "period_shots_sum")
        quality = session.get(db.GameQuality, (33169, UC))
    assert total is not None and total.not_checkable == 2
    assert quality is not None
    assert not (quality.lineup or quality.team_shots or quality.goalies or quality.goal_times)


def test_completeness_flags_on_the_reference_game(archive, sessions):
    audited(sessions, archive)

    with sessions() as session:
        uc = session.get(db.GameQuality, (33169, UC))
    assert uc is not None
    assert (uc.lineup, uc.goal_times, uc.team_shots, uc.period_shots, uc.goalies) == (
        True, True, True, True, True
    )
    assert uc.pp_opportunities is False, "0 opportunities recorded despite Kent State minors"
    assert uc.attendance is False


def test_audit_is_idempotent(archive, sessions):
    audited(sessions, archive)
    with sessions() as session:
        first = session.scalar(select(func.count()).select_from(db.ReconciliationFinding))
    run_audit(sessions)
    with sessions() as session:
        second = session.scalar(select(func.count()).select_from(db.ReconciliationFinding))
    assert first == second and first
