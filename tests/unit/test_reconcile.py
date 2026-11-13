"""Each known cause applies only when it accounts for the *whole* difference.

Built on hand-made Facts, so every case is exact and needs no fixture surgery.
"""

from __future__ import annotations

from typing import Any

from bca.audit.facts import (
    Facts,
    GameFacts,
    GoalTime,
    GoalTotals,
    SkaterTotals,
    Stint,
    TeamStats,
)
from bca.audit.reconcile import reconcile
from bca.db import schema as db

HOME, AWAY, GAME = 1, 2, 100


def facts(*, home_goals: int = 3, away_goals: int = 2, shootout: bool = False,
          **overrides: Any) -> Facts:
    """A clean full box score, HOME 3-2 AWAY, that passes every check."""
    game = GameFacts(GAME, 73, HOME, AWAY, home_goals, away_goals, shootout, "full", None)
    regulation = {HOME: home_goals - game.shootout_credit(HOME),
                  AWAY: away_goals - game.shootout_credit(AWAY)}
    base: dict[str, Any] = dict(
        games={GAME: game},
        team_stats={(GAME, t): TeamStats(30, g + game.shootout_credit(t), 0, 2, 4)
                    for t, g in regulation.items()},
        goals={(GAME, t): GoalTotals(goals=g, with_scorer=g, assists=g)
               for t, g in regulation.items()},
        skaters={(GAME, t): SkaterTotals(lines=18, goals=g, assists=g, penalty_minutes=4)
                 for t, g in regulation.items()},
        stints={(GAME, HOME): [Stint(11, 20 + regulation[AWAY], regulation[AWAY], 20, (3, 1200))],
                (GAME, AWAY): [Stint(22, 20 + regulation[HOME], regulation[HOME], 20, (3, 1200))]},
        penalty_minutes={(GAME, HOME): 4, (GAME, AWAY): 4},
        advantage_penalties={(GAME, HOME): 2, (GAME, AWAY): 2},
        periods={GAME: (regulation[HOME], 30, regulation[AWAY], 30)},
        appearances={}, rosters={}, md1=set(),
    )
    base.update(overrides)
    return Facts(**base)


def explained(f: Facts) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for finding in reconcile(f).findings:
        out.setdefault(finding.check, set()).add(finding.explanation)
    return out


def test_the_clean_game_has_no_findings():
    assert explained(facts()) == {}


def test_assist_credited_to_the_goalie():
    f = facts()
    f.skaters[(GAME, HOME)].assists -= 1
    f.goalie_assists = {(GAME, HOME): 1}
    assert explained(f) == {"assists_match": {"goalie_assist"}}


def test_goalie_assist_that_does_not_cover_the_gap_stays_unexplained():
    f = facts()
    f.skaters[(GAME, HOME)].assists -= 2
    f.goalie_assists = {(GAME, HOME): 1}
    assert explained(f) == {"assists_match": {"unexplained"}}


def test_shootout_winner_gets_one_extra_shot():
    f = facts(home_goals=3, away_goals=2, shootout=True)  # 2-2 after OT, HOME wins the SO
    f.team_stats[(GAME, HOME)].shots += 1
    assert explained(f) == {"period_shots_sum": {"shootout_winner_shot"}}


def test_extra_shot_for_the_shootout_loser_is_unexplained():
    f = facts(home_goals=3, away_goals=2, shootout=True)
    f.team_stats[(GAME, AWAY)].shots += 1
    assert explained(f) == {"period_shots_sum": {"unexplained"}}


def test_goal_after_the_goalie_left_but_not_flagged_empty_net():
    f = facts()
    # HOME's goalie left at 18:30 of the 3rd; AWAY's 2nd goal came at 19:40.
    f.stints[(GAME, HOME)] = [Stint(11, 21, 1, 20, (3, 1110))]
    f.goal_times = {(GAME, AWAY): [GoalTime(1, 300, False), GoalTime(3, 1180, False)]}
    assert explained(f) == {"goalie_ga_matches": {"unflagged_empty_net"}}


def test_goalie_shortfall_with_no_late_goal_is_unexplained():
    f = facts()
    f.stints[(GAME, HOME)] = [Stint(11, 21, 1, 20, (3, 1200))]
    assert explained(f) == {"goalie_ga_matches": {"unexplained"}}


def test_extra_power_play_goal_on_a_major():
    f = facts()
    f.team_stats[(GAME, HOME)].pp_goals = 3  # 3 PP goals on 2 opportunities
    f.goals[(GAME, HOME)].power_play = 3
    f.long_penalties = {(GAME, AWAY): 1}
    assert explained(f) == {"pp_goals_within_opportunities": {"major_penalty"}}


def test_extra_power_play_goal_with_no_major_is_unexplained():
    f = facts()
    f.team_stats[(GAME, HOME)].pp_goals = 3
    f.goals[(GAME, HOME)].power_play = 3
    assert explained(f) == {"pp_goals_within_opportunities": {"unexplained"}}


def test_partly_entered_box_score_explains_everything_in_it():
    f = facts()
    f.goals[(GAME, HOME)] = GoalTotals(goals=1, with_scorer=1, assists=1)  # 3 on the board
    f.periods[GAME] = (1, 30, 2, 30)

    found = explained(f)
    assert "goal_events_match_score" in found
    assert set().union(*found.values()) == {"partial_box_score"}


def line(player_id: int, kind: str = "skater", division: str = "MD1 X", **stats: Any):
    return db.UpstreamPlayerSeasonStats(
        season_id=73, team_id=HOME, player_id=player_id, kind=kind, name=f"P{player_id}",
        position="G" if kind == "goalie" else "F", division=division, **stats,
    )


def with_skater(f: Facts, goals: int) -> Facts:
    f.skater_seasons = {(73, HOME, 5): SkaterTotals(lines=1, goals=goals)}
    f.divisions = {(73, HOME): "MD1 X"}
    return f


def upstream_findings(f: Facts, check: str) -> set[str]:
    return {x.explanation for x in reconcile(f).findings if x.check == check}


def test_league_line_filed_under_the_other_squad():
    f = with_skater(facts(), goals=2)
    f.upstream = [line(5, division="MD2 X", games_played=9, goals=4, assists=0,
                       penalty_minutes=0)]
    assert upstream_findings(f, "upstream_skater_goals") == {"other_squad_games"}


def test_league_counts_the_shootout_decider():
    f = with_skater(facts(), goals=2)
    f.upstream = [line(5, games_played=1, goals=3, assists=0, penalty_minutes=0,
                       shootout_winning_goals=1)]
    assert upstream_findings(f, "upstream_skater_goals") == {"shootout_decider"}


def test_same_division_mismatch_stays_unexplained():
    f = with_skater(facts(), goals=2)
    f.upstream = [line(5, games_played=1, goals=4, assists=0, penalty_minutes=0,
                       shootout_winning_goals=1)]
    assert upstream_findings(f, "upstream_skater_goals") == {"unexplained"}


def test_off_roster_player_on_the_league_line_is_a_roster_feed_omission():
    f = facts()
    f.md1 = {(73, HOME)}
    f.appearances = {(73, HOME): {5, 6}}
    f.rosters = {(73, HOME): set()}
    f.upstream = [line(5, games_played=1, goals=0, assists=0, penalty_minutes=0)]
    found = {(x.player_id, x.explanation) for x in reconcile(f).findings
             if x.check == "player_on_roster"}
    assert found == {(5, "roster_feed_omission"), (6, "unexplained")}


def test_more_goal_events_than_the_score_is_not_partial():
    f = facts()
    f.goals[(GAME, HOME)] = GoalTotals(goals=5, with_scorer=3, assists=3)
    assert explained(f)["goal_events_match_score"] == {"unexplained"}
