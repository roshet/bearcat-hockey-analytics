"""The rebuilt ACHA M1 computer ranking (M6), on small hand-built seasons."""

from __future__ import annotations

from datetime import date
from itertools import count

import pytest

from bca.analytics.rankings import (
    PLACEHOLDER_TEAM,
    average_order,
    rank_table,
    ranked_games,
    rate,
)
from bca.db import schema as db

_ids = count(1)


def game(home: int, away: int, hg: int, ag: int, *, ot: bool = False, so: bool = False,
         on: date = date(2025, 10, 1)) -> db.Game:
    return db.Game(game_id=next(_ids), season_id=60, game_date=on, home_team_id=home,
                   visiting_team_id=away, home_goals=hg, visiting_goals=ag, is_final=True,
                   overtime=ot, shootout=so, status="Final", venue="")


TEAMS = [1, 2, 3, 4]
NAMES = {t: f"Team {t}" for t in [*TEAMS, 99]}


def round_robin(big: int = 9) -> list[db.Game]:
    return [game(1, 2, big, 0), game(2, 3, 3, 2), game(3, 4, 2, 1), game(1, 4, 3, 1),
            game(4, 2, 2, 2), game(3, 1, 1, 4)]


def test_a_round_robin_rates_by_capped_margin():
    a = rate(round_robin(), TEAMS, cap=7)
    assert a[1] > a[2] and a[1] > a[4]
    assert sum(a.values()) == pytest.approx(0)
    # A margin past the cap counts as the cap: 9-0 and 7-0 rate the same.
    assert rate(round_robin(big=7), TEAMS, cap=7) == pytest.approx(a)
    # Ranking B sees only who won: 9-0 rates like 1-0.
    b = rate(round_robin(), TEAMS, cap=1)
    assert rate(round_robin(big=1), TEAMS, cap=1) == pytest.approx(b)


def test_overtime_and_shootout_count_as_ties():
    tied = [game(1, 2, 2, 2), game(2, 3, 1, 0)]
    for extra in ({"ot": True}, {"so": True}):
        decided = [game(1, 2, 3, 2, **extra), game(2, 3, 1, 0)]
        assert rate(decided, [1, 2, 3], cap=7) == pytest.approx(rate(tied, [1, 2, 3], cap=7))


def test_the_average_rank_breaks_ties_on_ranking_b():
    # 1 and 2 both average 1.5; 2 is better on B, so 2 is ranked first.
    order = average_order({1: 1, 2: 2, 3: 3}, {1: 2, 2: 1, 3: 3})
    assert order == {2: 1, 1: 2, 3: 3}


def test_only_md1_vs_md1_games_count_and_tbd_is_never_a_team():
    keep = game(1, 2, 1, 0)
    games = [keep, game(1, 50, 9, 0), game(PLACEHOLDER_TEAM, 2, 1, 0)]
    md1 = {1, 2, PLACEHOLDER_TEAM}
    assert ranked_games(games, md1) == [keep]
    assert PLACEHOLDER_TEAM not in {r.team_id for r in rank_table(games, md1, NAMES)}


def test_as_of_keeps_games_on_or_before_the_date():
    early, on_day = game(1, 2, 1, 0, on=date(2025, 10, 1)), game(2, 1, 1, 0, on=date(2025, 10, 5))
    late = game(1, 2, 5, 0, on=date(2025, 10, 6))
    assert ranked_games([early, on_day, late], {1, 2}, as_of=date(2025, 10, 5)) == [early, on_day]


def test_a_team_without_md1_games_is_listed_unranked():
    rows = rank_table(round_robin(), {*TEAMS, 99}, NAMES)
    row = next(r for r in rows if r.team_id == 99)
    assert (row.games, row.rank, row.rank_a, row.rank_b, row.avg) == (0, None, None, None, None)
    assert rows[-1].team_id == 99
    assert [r.rank for r in rows[:-1]] == [1, 2, 3, 4]


def test_the_record_counts_ot_results_as_ties():
    games = [game(1, 2, 3, 1), game(2, 1, 3, 2, ot=True), game(3, 1, 4, 3, so=True)]
    row = next(r for r in rank_table(games, {1, 2, 3}, NAMES) if r.team_id == 1)
    assert (row.w, row.l, row.t, row.otw, row.otl) == (1, 0, 2, 0, 2)
    assert row.games == 3


def test_schedule_is_the_mean_opponent_rating_a():
    rows = {r.team_id: r for r in rank_table(round_robin(), set(TEAMS), NAMES)}
    # Team 1 played 2, 4 and 3 once each.
    expected = (rows[2].rating_a + rows[4].rating_a + rows[3].rating_a) / 3
    assert rows[1].schedule == pytest.approx(expected, abs=1e-3)


def test_the_season_loader_counts_every_final_game_and_keeps_the_rest_as_upcoming(sessions):
    from bca.analytics.rankings import load_ranking_season

    played, scored_only = game(1, 2, 3, 1), game(2, 1, 2, 0)
    scored_only.summary_normalized = False  # the box score never loaded; the score counts
    ahead = game(1, 2, 0, 0, on=date(2025, 12, 1))
    ahead.is_final = False
    elsewhere = game(1, 2, 1, 0)
    elsewhere.season_id = 46
    with sessions() as session:
        session.add_all([db.TeamSeason(team_id=t, season_id=60, name=f"MD1 Team {t}",
                                       division_name="MD1", is_md1=t != 3)
                         for t in (1, 2, 3)])
        session.add_all([played, scored_only, ahead, elsewhere])
        session.commit()
        season = load_ranking_season(session, 60)
    assert season.md1 == {1, 2}
    assert {g.game_id for g in season.games} == {played.game_id, scored_only.game_id}
    assert [g.game_id for g in season.upcoming] == [ahead.game_id]
    assert season.names[1] == "MD1 Team 1"
    assert season.label == "2025-26"


def test_forfeits_are_not_counted():
    # The ACHA's ranking matches better without them (docs/data-sources.md, "Rankings (M6)").
    played = game(1, 2, 3, 1)
    forfeit = game(2, 1, 1, 0)
    forfeit.status = "Visiting Forfeit"
    assert ranked_games([played, forfeit], {1, 2}) == [played]


# --- the history base rate (Task 4) --------------------------------------------------


def _ladder_season(n: int, early: date, late: date, swap: tuple[int, int] | None = None):
    """n teams in a strict ladder by ``early``; ``swap`` reverses one pair by ``late``."""
    games = [game(a, b, 3, 0, on=early) for a in range(1, n + 1) for b in range(a + 1, n + 1)]
    if swap:
        hi, lo = swap
        games += [game(lo, hi, 9, 0, on=late) for _ in range(6)]
    return games, set(range(1, n + 1)), early


def test_base_rate_counts_teams_in_the_band_that_finished_in_the_top_24():
    from bca.analytics.rankings import base_rate

    oct_ = date(2024, 10, 24)
    history = {
        "2023-24": _ladder_season(40, date(2023, 10, 24), date(2024, 2, 1)),
        # 20th in October falls below the line by the end; 28th climbs into it.
        "2024-25": _ladder_season(40, oct_, date(2025, 2, 1), swap=(20, 28)),
    }
    rate_ = base_rate(history, rank=24, on=date(2026, 10, 24))
    assert rate_ is not None
    assert (rate_.low, rate_.high, rate_.teams) == (19, 29, 22)
    # 2023-24: 19..24 stay above the line (6). 2024-25: 28 climbs in, 20 drops out (6).
    assert rate_.made_line == 12
    assert rate_.seasons == ["2023-24", "2024-25"] and rate_.on == "Oct 24"


def test_base_rate_is_withheld_below_10_teams():
    from bca.analytics.rankings import base_rate

    history = {"2023-24": _ladder_season(40, date(2023, 10, 24), date(2024, 2, 1))}
    assert base_rate(history, rank=24, on=date(2026, 10, 24), band=2) is None


def test_base_rate_band_is_clipped_at_the_top():
    from bca.analytics.rankings import base_rate

    history = {s: _ladder_season(40, date(y, 10, 24), date(y + 1, 2, 1))
               for s, y in (("2023-24", 2023), ("2024-25", 2024))}
    rate_ = base_rate(history, rank=2, on=date(2026, 10, 24))
    assert rate_ is not None and (rate_.low, rate_.high, rate_.teams) == (1, 7, 14)


def test_same_date_in_handles_the_new_year():
    from bca.analytics.rankings import same_date_in

    assert same_date_in(2023, date(2026, 10, 24)) == date(2023, 10, 24)
    assert same_date_in(2023, date(2027, 2, 14)) == date(2024, 2, 14)
    assert same_date_in(2023, date(2027, 2, 29 - 1)) == date(2024, 2, 28)
