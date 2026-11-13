"""Golden tests: real archived box scores, strength timelines computed by hand.

Every expected value below was worked out from the game's penalty and goal log
*before* the simulator was run on it; the comments are that working. Times are
seconds since the opening faceoff (P2 0:00 = 1200, P3 0:00 = 2400, OT = 3600).

Five of the six reproduce the scorekeeper's entered PP opportunities exactly.
32130 cannot, under any rule set: its entered 5/5 counts a flagged game
misconduct and leaves out two minors that gave UC a real advantage.
"""

from __future__ import annotations

import pytest

from bca.analytics.penalty_state import GameStrength, simulate
from bca.analytics.special_teams import from_box_score
from bca.ingest.parsers.game_summary import parse_game_summary
from tests.fixture_data import fixture_json

UC = 265


def simulated(game_id: int) -> GameStrength:
    raw = fixture_json(f"live/game_summary_{game_id}.json")
    return simulate(from_box_score(parse_game_summary(raw)))


def timeline(r: GameStrength) -> list[tuple[int, int, int, int]]:
    return [(s.start, s.end, s.home_skaters, s.visiting_skaters) for s in r.segments]


def opportunities(r: GameStrength, home: int, away: int) -> tuple[int, int]:
    return (r.teams[home].pp_opportunities, r.teams[away].pp_opportunities)


def pp_seconds(r: GameStrength, home: int, away: int) -> tuple[int, int]:
    return (r.teams[home].pp_seconds, r.teams[away].pp_seconds)


# --- 31418: Louisville 6-5 UC (OT). Power-play goals ending minors. ----------------


def test_31418_power_play_goals_end_minors_early():
    r = simulated(31418)
    # 403 LOU minor -> UC up 403-523.  1492 UC minor, LOU PP goal 1537 ends it.
    # 1756 UC minor -> LOU up 1756-1876.  3342 LOU minor, UC PP goal 3364 ends it.
    # OT winner at 4:37 of OT (3877) ends play.
    assert timeline(r) == [
        (0, 403, 5, 5), (403, 523, 4, 5), (523, 1492, 5, 5), (1492, 1537, 5, 4),
        (1537, 1756, 5, 5), (1756, 1876, 5, 4), (1876, 3342, 5, 5), (3342, 3364, 4, 5),
        (3364, 3877, 5, 5),
    ]
    assert opportunities(r, 347, UC) == (2, 2)  # entered: 2 / 2
    assert pp_seconds(r, 347, UC) == (45 + 120, 120 + 22)
    assert (r.teams[347].pp_goals, r.teams[UC].pp_goals) == (1, 1)


# --- 24759: UC 10-1 win on the road. Two-man advantage, even-up minors. --------------


def test_24759_five_on_three_and_minors_that_even_up():
    r = simulated(24759)
    home = 202
    # 319 home minor; UC PP goal 404 ends it.  431 UC minor -> home up; 480 home
    # minor makes it 4-on-4 until UC's ends at 551, then UC up until 600.
    # 2613 home minor; UC PP goal 2725 ends it.
    # 3483 two UC minors (different players) -> 5-on-3; home PP goal 3497 ends one.
    # 3600: two majors each side at the final horn cancel.
    assert timeline(r) == [
        (0, 319, 5, 5), (319, 404, 4, 5), (404, 431, 5, 5), (431, 480, 5, 4),
        (480, 551, 4, 4), (551, 600, 4, 5), (600, 2613, 5, 5), (2613, 2725, 4, 5),
        (2725, 3483, 5, 5), (3483, 3497, 5, 3), (3497, 3600, 5, 4),
    ]
    assert opportunities(r, home, UC) == (3, 3)  # entered: 3 / 3
    assert pp_seconds(r, home, UC) == (49 + 14 + 103, 85 + 49 + 112)
    assert [g.situation for g in r.goals if g.team_id == home] == ["pp"]
    five_on_three = next(g for g in r.goals if g.team_id == home)
    assert (five_on_three.skaters_for, five_on_three.skaters_against) == (5, 3)


# --- 13015: UC 0-14 on the road. A double minor, coincidentals. -----------------------


def test_13015_double_minor_and_coincidentals():
    r = simulated(13015)
    home = 5
    # 1325 one minor each: cancelled.  1604 home minor, 1645 UC minor: UC up
    # 41 s, 4-on-4, then home up 1724-1765.  2821 UC minor; home PP goal 2905.
    # 3183 four misconducts: nothing.  3240 two minors to one home player:
    # served back to back, 3240-3480 (an SH goal at 3387 changes nothing).
    assert timeline(r) == [
        (0, 364, 5, 5), (364, 484, 5, 4), (484, 608, 5, 5), (608, 728, 4, 5),
        (728, 862, 5, 5), (862, 982, 4, 5), (982, 1604, 5, 5), (1604, 1645, 4, 5),
        (1645, 1724, 4, 4), (1724, 1765, 5, 4), (1765, 2138, 5, 5), (2138, 2258, 5, 4),
        (2258, 2821, 5, 5), (2821, 2905, 5, 4), (2905, 3240, 5, 5), (3240, 3480, 4, 5),
        (3480, 3600, 5, 5),
    ]
    assert opportunities(r, home, UC) == (4, 5)  # entered: 4 / 5
    assert pp_seconds(r, home, UC) == (120 + 41 + 120 + 84, 120 + 120 + 41 + 240)
    assert (r.teams[home].sh_goals, r.teams[home].pp_goals) == (2, 1)


# --- 32953: shootout game. A penalty in overtime. --------------------------------------


def test_32953_overtime_penalty():
    r = simulated(32953)
    home, away = 391, 347
    # 1609 home bench minor; away PP goal 1698 ends it.  2208 and 2661:
    # coincidental minors (one with misconducts) cancel.  3770 home minor at
    # 2:50 of OT runs to 3890; OT ends at 5:00 (3900) for the shootout.
    assert timeline(r) == [
        (0, 650, 5, 5), (650, 770, 5, 4), (770, 1609, 5, 5), (1609, 1698, 4, 5),
        (1698, 2032, 5, 5), (2032, 2152, 4, 5), (2152, 2861, 5, 5), (2861, 2981, 4, 5),
        (2981, 3770, 5, 5), (3770, 3890, 4, 5), (3890, 3900, 5, 5),
    ]
    assert opportunities(r, home, away) == (1, 4)  # entered: 1 / 4
    assert pp_seconds(r, home, away) == (120, 89 + 120 + 120 + 120)


# --- 31972: a delayed third penalty. -----------------------------------------------------


def test_31972_a_third_penalty_waits_for_a_box_place():
    r = simulated(31972)
    home, away = 422, 415
    # 683 away major (+GM) and 818 minor: 5-on-3 until 938.
    # 2816 and 2843 away minors: 5-on-3.  2898 a third: it waits until 2936,
    # so 5-on-3 lasts to 2963.  3031 another: 5-on-3 again until 3056.
    # 3256 away goal and away minor at one instant: the goal is at even
    # strength, then the minor starts.  3312 home minor: 4-on-4, then away up.
    assert timeline(r) == [
        (0, 285, 5, 5), (285, 294, 5, 4), (294, 558, 5, 5), (558, 678, 5, 4),
        (678, 683, 5, 5), (683, 818, 5, 4), (818, 938, 5, 3), (938, 983, 5, 4),
        (983, 1200, 5, 5), (1200, 1320, 4, 5), (1320, 1511, 5, 5), (1511, 1631, 5, 4),
        (1631, 2283, 5, 5), (2283, 2403, 4, 5), (2403, 2816, 5, 5), (2816, 2843, 5, 4),
        (2843, 2963, 5, 3), (2963, 3031, 5, 4), (3031, 3056, 5, 3), (3056, 3151, 5, 4),
        (3151, 3256, 5, 5), (3256, 3312, 5, 4), (3312, 3376, 4, 4), (3376, 3432, 4, 5),
        (3432, 3600, 5, 5),
    ]
    assert opportunities(r, home, away) == (10, 3)  # entered: 10 / 3
    assert pp_seconds(r, home, away) == (940, 296)
    delayed = [pid for pid, o in r.penalties.items() if o.delayed]
    assert len(delayed) == 1


# --- 32130: UC 1-11 on the road. The hard case. -----------------------------------------


def test_32130_major_game_misconduct_and_coincidental_minor():
    r = simulated(32130)
    home = 5
    # 1407 UC minor, 1452 home minor: home up 45 s, 4-on-4, UC up 1527-1572.
    # 3164 a home minor + a UC major and game misconduct. Not
    # like for like, so nothing cancels: 4-on-4 until 3284.
    # 3252 home minor while that home minor still runs: UC up 3252-3284, 4-on-4 to 3372,
    # then home up until the major ends at 3464 -- PP goals at 3403, 3445 and
    # 3462 do not end a major.  3486 home major (+GM) runs to the final horn.
    assert timeline(r) == [
        (0, 283, 5, 5), (283, 403, 4, 5), (403, 1188, 5, 5), (1188, 1308, 5, 4),
        (1308, 1407, 5, 5), (1407, 1452, 5, 4), (1452, 1527, 4, 4), (1527, 1572, 4, 5),
        (1572, 2790, 5, 5), (2790, 2910, 4, 5), (2910, 2947, 5, 5), (2947, 3067, 4, 5),
        (3067, 3164, 5, 5), (3164, 3252, 4, 4), (3252, 3284, 3, 4), (3284, 3372, 4, 4),
        (3372, 3464, 5, 4), (3464, 3486, 5, 5), (3486, 3600, 4, 5),
    ]
    # Entered 5 / 5. Home: 3 real advantages + a game misconduct the scorekeeper
    # flagged. UC: the 3164 home minor and the charging minor both gave UC 32 s up a man.
    assert opportunities(r, home, UC) == (3, 7)
    assert pp_seconds(r, home, UC) == (120 + 45 + 92, 120 + 45 + 120 + 120 + 32 + 114)
    assert (r.teams[home].pp_goals, r.teams[home].sh_goals) == (3, 1)


# --- 33169: UC 7-2 Kent State. A fight with one major entered twice. -----------


def test_33169_a_major_entered_twice_does_not_tip_the_coincidental_majors():
    summary = parse_game_summary(
        fixture_json("live/game_summary.json")
    )
    r = simulate(from_box_score(summary))
    # 3005 UC cross-checking minor -> KSU up until 3125.
    # 3075 fight. UC: #43401 fighting; #34278 face mask (entered twice, as a 5
    # and as a 15 "w/Game Misconduct") and fighting; plus a roughing minor.
    # KSU: #21319 face mask and fighting; #34393 fighting. Three majors each
    # cancel; the roughing minor stands: 3-on-5 until 3125, 4-on-5 until 3195.
    # Counting the duplicate left UC a man short for five more minutes.
    assert [s for s in timeline(r) if s[0] >= 3000] == [
        (3005, 3075, 4, 5), (3075, 3125, 3, 5), (3125, 3195, 4, 5), (3195, 3600, 5, 5),
    ]
    majors = [p.penalty_id for p in summary.penalties if p.minutes in (5, 15)]
    assert not any(r.penalties[pid].created_advantage for pid in majors)


def test_32130_pp_goals_are_attributed_to_the_major_being_served():
    r = simulated(32130)
    raw = fixture_json("live/game_summary_32130.json")
    summary = parse_game_summary(raw)
    major = next(p.penalty_id for p in summary.penalties if p.description == "Major-Cross Checking")
    pp = [g for g in r.goals if g.situation == "pp"]
    assert len(pp) == 3  # 3403, 3445, 3462: all during the UC major
    assert all(g.serving_penalty_ids == (major,) for g in pp)
    assert all(g.serving_penalty_ids == () for g in r.goals if g.situation != "pp")


def test_24759_five_on_three_goal_lists_both_minors():
    r = simulated(24759)
    goal = next(g for g in r.goals if g.team_id == 202)
    assert len(goal.serving_penalty_ids) == 2


@pytest.mark.parametrize("game_id", [31418, 24759, 13015, 32953, 31972, 32130])
def test_golden_games_raise_no_simulation_issues(game_id):
    assert simulated(game_id).issues == []
