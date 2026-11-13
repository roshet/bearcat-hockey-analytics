"""The penalty-state simulator, rule by rule, then on real games computed by hand.

Times are seconds since the opening faceoff; HOME and AWAY are made-up ids.
"""

from __future__ import annotations

import pytest

from bca.analytics.penalty_state import (
    GameInput,
    Goal,
    Kind,
    Penalty,
    Rules,
    Segment,
    classify,
    game_time,
    simulate,
)

HOME, AWAY = 1, 2
_ids = iter(range(1, 10_000))


def minor(time: int, team: int = HOME, player: int | None = None, minutes: int = 2,
          description: str = "Tripping") -> Penalty:
    return Penalty(next(_ids), time, team, minutes, description,
                   player if player is not None else next(_ids))


def goal(time: int, team: int = AWAY, empty_net: bool = False) -> Goal:
    return Goal(next(_ids), time, team, empty_net)


def run(*events: Penalty | Goal, rules: Rules | None = None, end: int = 3600):
    game = GameInput(
        game_id=1,
        home_team_id=HOME,
        visiting_team_id=AWAY,
        penalties=tuple(e for e in events if isinstance(e, Penalty)),
        goals=tuple(e for e in events if isinstance(e, Goal)),
        end_time=end,
    )
    return simulate(game, rules)


def strengths(result) -> list[tuple[int, int, int, int]]:
    return [(s.start, s.end, s.home_skaters, s.visiting_skaters) for s in result.segments]


# --- classification ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("minutes", "description", "parts"),
    [
        (2, "Tripping", [Kind.MINOR]),
        (5, "Major-Boarding", [Kind.MAJOR]),
        (10, "10-Minute Misconduct", []),
        (10, "Game Misconduct", []),
        (12, "Checking From Behind w/Misconduct", [Kind.MINOR]),
        (15, "Major-Spearing w/Game Misconduct", [Kind.MAJOR]),
        (15, "Fighting-Major- w/Disqualification DQ", [Kind.MAJOR]),
        (2, "Penalty Shot-Tripping", []),
    ],
)
def test_classify(minutes, description, parts):
    assert classify(minutes, description) == parts


def test_unknown_penalty_length_fails_loudly():
    with pytest.raises(ValueError, match="penalty length"):
        classify(4, "Double Minor-High Sticking")


def test_game_time_runs_continuously_into_overtime():
    assert (game_time(1, 0), game_time(3, 1199), game_time(4, 23)) == (0, 3599, 3623)


# --- the rules ---------------------------------------------------------------------


def test_a_single_minor_runs_its_two_minutes():
    r = run(minor(100))

    assert strengths(r) == [(0, 100, 5, 5), (100, 220, 4, 5), (220, 3600, 5, 5)]
    assert (r.teams[AWAY].pp_opportunities, r.teams[AWAY].pp_seconds) == (1, 120)
    assert (r.teams[HOME].pp_opportunities, r.teams[HOME].pk_seconds) == (0, 120)


def test_a_power_play_goal_ends_the_minor():
    r = run(minor(100), goal(160))

    assert strengths(r) == [(0, 100, 5, 5), (100, 160, 4, 5), (160, 3600, 5, 5)]
    assert (r.teams[AWAY].pp_seconds, r.teams[AWAY].pp_goals) == (60, 1)
    assert [g.situation for g in r.goals] == ["pp"]


def test_a_short_handed_goal_does_not_end_anything():
    r = run(minor(100), goal(160, team=HOME))

    assert r.teams[AWAY].pp_seconds == 120
    assert (r.teams[HOME].sh_goals, r.teams[AWAY].sh_goals_against) == (1, 1)


def test_coincidental_minors_cancel_and_stay_at_full_strength():
    r = run(minor(100, HOME), minor(100, AWAY))

    assert strengths(r) == [(0, 3600, 5, 5)]
    assert r.teams[HOME].pp_opportunities == r.teams[AWAY].pp_opportunities == 0


def test_coincidental_minors_can_play_four_on_four():
    r = run(minor(100, HOME), minor(100, AWAY), rules=Rules(coincidental_four_on_four=True))

    assert strengths(r) == [(0, 100, 5, 5), (100, 220, 4, 4), (220, 3600, 5, 5)]
    assert r.teams[HOME].pp_seconds == r.teams[AWAY].pp_seconds == 0


def test_two_man_advantage():
    r = run(minor(100), minor(130))

    assert strengths(r) == [
        (0, 100, 5, 5), (100, 130, 4, 5), (130, 220, 3, 5), (220, 250, 4, 5), (250, 3600, 5, 5)
    ]
    assert (r.teams[AWAY].pp_opportunities, r.teams[AWAY].pp_seconds) == (2, 150)


def test_a_power_play_goal_at_five_on_three_ends_the_older_minor():
    r = run(minor(100), minor(130), goal(150))

    # The 100s minor (70s left) ends; the 130s minor runs to 250.
    assert strengths(r) == [
        (0, 100, 5, 5), (100, 130, 4, 5), (130, 150, 3, 5), (150, 250, 4, 5), (250, 3600, 5, 5)
    ]


def test_a_third_penalty_waits_for_a_box_place():
    r = run(minor(100), minor(110), minor(120))

    # Never below three skaters: the 120s minor starts when the 100s one ends.
    assert strengths(r) == [
        (0, 100, 5, 5), (100, 110, 4, 5), (110, 230, 3, 5), (230, 340, 4, 5), (340, 3600, 5, 5)
    ]
    assert r.teams[AWAY].pp_opportunities == 3


def test_a_double_minor_is_served_back_to_back():
    r = run(minor(100, player=7), minor(100, player=7))

    assert strengths(r) == [(0, 100, 5, 5), (100, 340, 4, 5), (340, 3600, 5, 5)]
    # Each half is its own penalty event, served on a man advantage, so the
    # default counts two -- as the league's scorekeepers flag each half.
    assert r.teams[AWAY].pp_opportunities == 2
    assert run(minor(100, player=7), minor(100, player=7),
               rules=Rules(opportunity="per_penalty")).teams[AWAY].pp_opportunities == 1


def test_a_power_play_goal_ends_only_the_current_half_of_a_double_minor():
    r = run(minor(100, player=7), minor(100, player=7), goal(150))

    assert strengths(r) == [(0, 100, 5, 5), (100, 270, 4, 5), (270, 3600, 5, 5)]


def test_a_double_minor_can_count_as_two_opportunities_per_penalty():
    r = run(minor(100, player=7), minor(100, player=7),
            rules=Rules(opportunity="per_penalty", double_minor_opportunities=2))
    assert r.teams[AWAY].pp_opportunities == 2


def test_one_players_major_entered_twice_counts_once():
    # Game 25444: "Major-Grab Face Mask" (5) and "... w/Disqualification DQ"
    # (15) for one player at one instant. One man down for 5:00, not two.
    r = run(minor(100, player=7, minutes=5, description="Major-Grab Face Mask"),
            minor(100, player=7, minutes=15,
                  description="Major-Grab Face Mask w/Disqualification DQ"),
            goal(150))

    assert strengths(r) == [(0, 100, 5, 5), (100, 400, 4, 5), (400, 3600, 5, 5)]
    assert [(g.skaters_for, g.skaters_against) for g in r.goals] == [(5, 4)]
    assert r.teams[AWAY].pp_opportunities == 1


def test_a_disqualification_with_major_is_part_of_that_major():
    r = run(minor(100, player=7, minutes=5, description="Major-Checking from Behind"),
            minor(100, player=7, minutes=15, description="Disqualification w/Major"))

    assert strengths(r) == [(0, 100, 5, 5), (100, 400, 4, 5), (400, 3600, 5, 5)]


def test_two_different_majors_both_count_then_are_served_back_to_back():
    # Game 7473: one player's cross-checking and fighting majors, the other side
    # one fighting major. The fights cancel; the cross-check is a power play.
    r = run(minor(100, player=7, minutes=15, description="Major-Cross Checking w/Game Misconduct"),
            minor(100, player=7, minutes=15, description="Fighting-Major- w/Disqualification DQ"),
            minor(100, team=AWAY, player=8, minutes=15,
                  description="Fighting-Major- w/Disqualification DQ"))

    assert strengths(r) == [(0, 100, 5, 5), (100, 400, 4, 5), (400, 3600, 5, 5)]


def test_one_players_major_and_minor_are_served_back_to_back():
    r = run(minor(100, player=7, minutes=5, description="Major-Boarding"), minor(100, player=7),
            goal(450), goal(500))

    # The major first (not ended by a goal), then the minor, which the first
    # power-play goal ends: never two men down.
    assert strengths(r) == [(0, 100, 5, 5), (100, 450, 4, 5), (450, 3600, 5, 5)]
    assert [g.situation for g in r.goals] == ["pp", "ev"]
    assert r.teams[AWAY].pp_opportunities == 2


def test_one_players_penalties_were_concurrent_before_calibration():
    r = run(minor(100, player=7, minutes=5, description="Major-Boarding"), minor(100, player=7),
            rules=Rules(same_player="concurrent"))

    assert strengths(r) == [(0, 100, 5, 5), (100, 220, 3, 5), (220, 400, 4, 5),
                            (400, 3600, 5, 5)]


def test_the_penalty_being_served_follows_a_players_queue():
    major = minor(100, player=7, minutes=5, description="Major-Boarding")
    trip = minor(100, player=7)
    r = run(major, trip, goal(200), goal(450))

    assert [g.serving_penalty_ids for g in r.goals] == [(major.penalty_id,), (trip.penalty_id,)]


def test_a_major_is_not_ended_by_power_play_goals():
    r = run(minor(100, minutes=5, description="Major-Boarding"), goal(150), goal(200))

    assert strengths(r) == [(0, 100, 5, 5), (100, 400, 4, 5), (400, 3600, 5, 5)]
    assert (r.teams[AWAY].pp_goals, r.teams[AWAY].pp_opportunities) == (2, 1)


def test_major_with_game_misconduct_gives_five_minutes_only():
    r = run(minor(100, minutes=15, description="Major-Spearing w/Game Misconduct"))
    assert r.teams[AWAY].pp_seconds == 300


@pytest.mark.parametrize(
    ("minutes", "description"),
    [(10, "10-Minute Misconduct"), (2, "Penalty Shot-Tripping")],
)
def test_misconducts_and_penalty_shots_give_no_advantage(minutes, description):
    r = run(minor(100, minutes=minutes, description=description))
    assert strengths(r) == [(0, 3600, 5, 5)]
    assert r.teams[AWAY].pp_opportunities == 0


def test_coincidental_majors_cancel_and_the_extra_minor_is_a_power_play():
    r = run(minor(100, HOME, player=3, minutes=5, description="Major-Fighting"),
            minor(100, HOME, player=4),
            minor(100, AWAY, player=5, minutes=5, description="Major-Fighting"))

    assert strengths(r) == [(0, 100, 5, 5), (100, 220, 4, 5), (220, 3600, 5, 5)]
    assert (r.teams[AWAY].pp_opportunities, r.teams[HOME].pp_opportunities) == (1, 0)


def test_a_double_minor_against_a_minor_leaves_one_minor():
    r = run(minor(100, HOME, player=7), minor(100, HOME, player=7), minor(100, AWAY))

    assert strengths(r) == [(0, 100, 5, 5), (100, 220, 4, 5), (220, 3600, 5, 5)]


def test_a_cancelled_half_is_not_credited_with_the_advantage():
    """Real shape (game 125, 3rd 3:11): two minors to one player against one minor."""
    cross_check = minor(100, HOME, player=7, description="Cross Checking")
    roughing = minor(100, HOME, player=7, description="Roughing")
    r = run(cross_check, roughing, minor(100, AWAY, description="Roughing"))

    credited = [r.penalties[p.penalty_id].created_advantage for p in (cross_check, roughing)]
    assert sorted(credited) == [False, True]
    assert r.teams[AWAY].pp_seconds == 120


def test_power_play_spells_can_be_counted_instead_of_penalties():
    r = run(minor(100), minor(130), rules=Rules(opportunity="per_episode"))
    assert r.teams[AWAY].pp_opportunities == 1


def test_a_penalty_carries_into_the_next_period():
    r = run(minor(game_time(1, 1150)))
    assert strengths(r)[1] == (1150, 1270, 4, 5)


def test_three_on_three_overtime_penalty_adds_a_skater():
    r = run(minor(game_time(4, 60)), rules=Rules(ot_skaters=3), end=3900)

    assert strengths(r) == [(0, 3600, 5, 5), (3600, 3660, 3, 3), (3660, 3780, 3, 4),
                            (3780, 3900, 3, 3)]


def test_events_after_play_ended_are_reported_not_simulated():
    r = run(minor(3700), goal(3800), end=3600)
    assert len(r.issues) == 2
    assert strengths(r) == [(0, 3600, 5, 5)]


def test_penalty_outcomes_say_what_each_penalty_did():
    first, cancelled_home, cancelled_away = minor(100), minor(500, HOME), minor(500, AWAY)
    misconduct = minor(900, minutes=10, description="10-Minute Misconduct")
    r = run(first, cancelled_home, cancelled_away, misconduct)

    o = r.penalties
    assert (o[first.penalty_id].cancelled, o[first.penalty_id].advantage_seconds) == (False, 120)
    assert o[cancelled_home.penalty_id].cancelled and o[cancelled_away.penalty_id].cancelled
    assert o[misconduct.penalty_id].cancelled
    assert not o[misconduct.penalty_id].created_advantage


def test_a_penalty_that_only_evens_things_up_creates_no_advantage():
    away_minor, home_minor = minor(100, AWAY), minor(150, HOME)
    r = run(away_minor, home_minor)

    # 5-on-4 HOME until 150, then 4-on-4 until 220, then AWAY ahead 220-270.
    assert r.penalties[home_minor.penalty_id].advantage_seconds == 50


def test_the_delayed_third_penalty_is_marked():
    third = minor(120)
    r = run(minor(100), minor(110), third)
    assert r.penalties[third.penalty_id].delayed


def test_by_time_a_minor_cancels_two_minutes_of_a_major():
    major = minor(100, AWAY, minutes=5, description="Major-Cross Checking")
    home_minor = minor(100, HOME)
    r = run(major, home_minor, rules=Rules(coincidental="by_time"))

    assert strengths(r) == [(0, 100, 5, 5), (100, 280, 5, 4), (280, 3600, 5, 5)]
    assert r.penalties[home_minor.penalty_id].cancelled
    assert (r.teams[HOME].pp_opportunities, r.teams[AWAY].pp_opportunities) == (1, 0)


def test_like_for_like_a_minor_and_a_major_both_count():
    major = minor(100, AWAY, minutes=5, description="Major-Cross Checking")
    r = run(major, minor(100, HOME))

    assert strengths(r) == [(0, 100, 5, 5), (100, 220, 4, 4), (220, 400, 5, 4),
                            (400, 3600, 5, 5)]
    # The home minor only ever made it 4-on-4: an opportunity per penalty,
    # but not by default, which counts only real advantages.
    assert (r.teams[HOME].pp_opportunities, r.teams[AWAY].pp_opportunities) == (1, 0)
    per_penalty = run(major, minor(100, HOME), rules=Rules(opportunity="per_penalty"))
    assert (per_penalty.teams[HOME].pp_opportunities,
            per_penalty.teams[AWAY].pp_opportunities) == (1, 1)


def test_segments_cover_the_game_exactly():
    r = run(minor(100), minor(130), goal(150), minor(500, AWAY), minor(500, HOME))
    assert r.segments[0].start == 0 and r.segments[-1].end == 3600
    assert all(a.end == b.start for a, b in zip(r.segments, r.segments[1:], strict=False))
    assert all(isinstance(s, Segment) for s in r.segments)
