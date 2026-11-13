"""Special teams & discipline detail, on real games (UC's 2026-27 fixtures)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.scouting import load_season
from bca.analytics.special_teams import run_special_teams
from bca.analytics.team_detail import (
    TeamDetailReport,
    _short_seconds,
    build_detail_reports,
    display_player,
    likely_range,
)
from bca.audit.run import run_audit
from bca.db import schema as db
from bca.ingest import endpoints
from bca.ingest.archive import RawArchive
from bca.ingest.normalize import Normalizer
from tests.unit.test_normalize import UC, archive_payload, build_archive, payload

KENTUCKY = 346
NOW = datetime(2026, 10, 23, tzinfo=UTC)
FIXTURE_GAMES = (33157, 33171, 32130, 31980, 33170)


@pytest.fixture
def prepared(tmp_path, sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    archive: RawArchive = build_archive(tmp_path / "cache")
    for game_id in FIXTURE_GAMES:
        archive_payload(archive, endpoints.game_summary(game_id),
                        payload(f"game_summary_{game_id}"))
    Normalizer(sessions, archive).run()
    run_audit(sessions)
    run_special_teams(sessions)
    return sessions


@pytest.fixture
def reports(prepared) -> dict[int, TeamDetailReport]:
    with prepared() as session:
        return {r.team_id: r for r in build_detail_reports(session, 73, [UC, KENTUCKY], now=NOW)}


def test_the_game_log_ties_out_to_the_simulator(prepared, reports):
    uc = reports[UC].current
    with prepared() as session:
        for row in uc.game_log:
            stored = session.get(db.TeamGameSpecialTeams, (row.game_id, UC))
            if stored is None:
                assert not row.simulated and row.flag
                continue
            assert (row.pp_goals, row.pp_opportunities) == (stored.pp_goals,
                                                           stored.pp_opportunities)
            assert row.pk_minutes == round(stored.pk_seconds / 60, 1)


def test_32130_pp_goals_against_name_a_uc_major_by_number(reports):
    uc = reports[UC].current
    in_32130 = [g for g in uc.pp_goals_against if g.game_id == 32130]
    assert [g.clock for g in in_32130] == ["16:43", "17:25", "17:42"]
    assert all(g.strength == "5v4" for g in in_32130)
    assert all([(p.infraction, p.player) for p in g.serving] == [("Cross checking", "#4")]
               for g in in_32130)


def test_uc_shows_no_player_names_opponents_do(prepared, reports):
    uc, ky = reports[UC], reports[KENTUCKY]
    assert uc.players_as_numbers and not ky.players_as_numbers
    assert all(p.player.startswith("#") for p in uc.current.players)
    served = [p.player for g in uc.current.pp_goals_against for p in g.serving]
    assert served and all(name.startswith("#") or name == "bench" for name in served)
    with prepared() as session:
        major = session.get(db.Player, 43406)  # the UC player with game 32130's major
    assert major is not None and major.last_name not in uc.model_dump_json()
    assert any(not p.player.startswith("#") for p in ky.current.players)


def test_every_pp_goal_against_lists_a_penalty_being_served(reports):
    for report in reports.values():
        for goal in report.current.pp_goals_against:
            assert goal.serving


def test_player_table_shows_the_short_handed_minutes_each_player_caused(prepared, reports):
    uc = reports[UC].current
    with prepared() as session:
        major_player = session.get(db.Player, 43406)
        assert major_player is not None
        seconds = sum(session.scalars(
            select(db.PenaltyOutcome.advantage_seconds)
            .join(db.PenaltyEvent, (db.PenaltyEvent.game_id == db.PenaltyOutcome.game_id)
                  & (db.PenaltyEvent.penalty_id == db.PenaltyOutcome.penalty_id))
            .where(db.PenaltyEvent.taken_by_id == major_player.player_id)
        ))
    assert seconds > 0
    number_4 = next(p for p in uc.players if p.player == "#4")
    assert number_4.sh_minutes_caused == round(seconds / 60, 1)
    # Every short-handed second traces to a penalty (bench minors aside); at
    # 5-on-3 two penalties share it, so the players' total can run higher.
    caused = sum(p.sh_minutes_caused for p in uc.players)
    assert caused >= 0.9 * uc.summary.sh_minutes_per_game * uc.summary.games


def test_a_double_minor_counts_its_short_handed_time_once():
    halves = [db.PenaltyEvent(game_id=1, penalty_id=i, team_id=UC, period=2, seconds=600,
                              minutes=2, description="High Sticking", taken_by_id=4)
              for i in (1, 2)]
    major = db.PenaltyEvent(game_id=1, penalty_id=3, team_id=UC, period=3, seconds=60,
                            minutes=5, description="Major-Boarding", taken_by_id=4)
    outcomes = {i: db.PenaltyOutcome(game_id=1, penalty_id=i, team_id=UC, cancelled=False,
                                     delayed=False, advantage_seconds=sec,
                                     flagged_power_play=True)
                for i, sec in ((1, 240), (2, 240), (3, 300))}

    assert _short_seconds([*halves, major], outcomes) == {4: 240 + 300}


def test_unsimulated_games_are_flagged_not_counted(reports):
    uc = reports[UC].current
    unsimulated = [r for r in uc.game_log if not r.simulated]
    assert all(r.flag and r.pp_opportunities == 0 for r in unsimulated)
    assert uc.summary.games == len(uc.game_log) - len(unsimulated)


def test_summary_matches_the_game_log(reports):
    uc = reports[UC].current
    log = [r for r in uc.game_log if r.simulated]
    assert uc.summary.pp_goals == sum(r.pp_goals for r in log)
    assert uc.summary.pk_opportunities == sum(r.pk_opportunities for r in log)


def test_likely_range_is_the_80pc_wilson_interval():
    low, high = likely_range(8, 50) or (0, 0)
    assert (low, high) == pytest.approx((0.1045, 0.2371), abs=1e-4)  # UC 2026-27 PP
    assert likely_range(0, 0) is None
    low0, high0 = likely_range(0, 10) or (1, 1)
    assert low0 == 0 and 0 < high0 < 0.2


def test_shared_jersey_numbers_are_told_apart(prepared):
    with prepared() as session:
        season = load_season(session, 73)
    season.jerseys[(UC, 900001)] = 4
    season.jerseys[(UC, 900002)] = 4
    shown = {display_player(season, UC, p) for p, n in
             [(pid, n) for (t, pid), n in season.jerseys.items() if t == UC and n == 4]}
    assert len(shown) == len([1 for (t, _), n in season.jerseys.items() if t == UC and n == 4])
    assert all(s.startswith("#4") for s in shown)
    assert display_player(season, KENTUCKY, 900001) != "#4"  # opponents: names, not numbers


def test_the_leagues_own_pp_count_is_summed_for_the_season_not_flagged_per_game(prepared,
                                                                              reports):
    uc = reports[UC].current
    with prepared() as session:
        stored = [session.get(db.TeamGameSpecialTeams, (row.game_id, UC))
                  for row in uc.game_log if row.simulated]
    entered = [s.entered_pp_opportunities for s in stored if s is not None]
    assert uc.summary.league_pp_opportunities == sum(e or 0 for e in entered)
    assert uc.summary.games_not_entered == sum(e is None for e in entered)
    assert uc.summary.games_counted_differently == sum(
        s is not None and s.entered_pp_opportunities is not None
        and s.entered_pp_opportunities != s.pp_opportunities for s in stored)
    assert not any(row.flag and "PP opportunities" in row.flag
                   for report in reports.values() for row in report.current.game_log)
