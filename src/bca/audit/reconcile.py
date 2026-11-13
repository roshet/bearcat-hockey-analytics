"""The reconciliation suite: where the data disagrees with itself, or with the league.

Every check yields findings with an ``explanation``: a known cause, or
``unexplained``. A cause is added here only after looking at real cases -- never
assumed in advance -- and is named in docs/data-sources.md.

Score-only box scores are counted as *not checkable* for anything beyond the
score itself, never as passes: their zeros mean "not entered".
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field, replace

from bca.audit.facts import Facts, GameFacts, GoalTotals, SkaterTotals

UNEXPLAINED = "unexplained"

#: Known causes. Each is documented in docs/data-sources.md, and each applies
#: only when it accounts for the *whole* difference.
EMPTY_NET = "empty_net"
UNFLAGGED_EMPTY_NET = "unflagged_empty_net"
UNENTERED_ZERO = "unentered_zero"
MAJOR_PENALTY = "major_penalty"
GOALIE_ASSIST = "goalie_assist"
SHOOTOUT_WINNER_SHOT = "shootout_winner_shot"
PARTIAL_BOX_SCORE = "partial_box_score"
UPSTREAM_ONLY_PLAYER = "upstream_only_player"
#: The league's line is filed under another division: it also counts games
#: for the school's other squad, which are no MD1 team's games.
OTHER_SQUAD_GAMES = "other_squad_games"
#: The difference is exactly the line's shootout winners (skater) or losses
#: (goalie): the league counts the deciding shootout goal; box scores do not.
SHOOTOUT_DECIDER = "shootout_decider"
#: Missing from the roster feed, but on the league's own season line for the
#: team: the roster feed is the incomplete source, not the box score.
ROSTER_FEED_OMISSION = "roster_feed_omission"


@dataclass(frozen=True)
class Finding:
    check: str
    explanation: str
    detail: str
    season_id: int | None = None
    game_id: int | None = None
    team_id: int | None = None
    player_id: int | None = None
    expected: int | None = None
    actual: int | None = None


@dataclass
class CheckTotal:
    unit: str
    checked: int = 0
    not_checkable: int = 0


@dataclass
class Reconciliation:
    findings: list[Finding] = field(default_factory=list)
    totals: dict[str, CheckTotal] = field(default_factory=dict)

    def total(self, check: str, unit: str) -> CheckTotal:
        return self.totals.setdefault(check, CheckTotal(unit))


def reconcile(facts: Facts) -> Reconciliation:
    out = Reconciliation()
    for check in (
        _score_matches_box,
        _goal_events_match_score,
        _period_goals_sum,
        _period_shots_sum,
        _goalies_recorded,
        _goalie_ga_matches,
        _goalie_saves,
        _skater_goals_match,
        _assists_match,
        _pim_matches,
        _pp_goals_within_opportunities,
        _pp_goal_flags_match,
        _player_on_roster,
        _upstream_goalies,
        _upstream_skaters,
    ):
        check(facts, out)

    # A partly entered box score fails nearly every check at once; one cause,
    # surfaced once per game in game_quality, not dozens of separate mysteries.
    partial = {g.game_id for g in facts.games.values() if facts.is_partial(g)}
    out.findings = [
        replace(f, explanation=PARTIAL_BOX_SCORE)
        if f.game_id in partial and f.explanation == UNEXPLAINED
        else f
        for f in out.findings
    ]
    return out


# --- helpers ------------------------------------------------------------------


def _team_game_checks(
    facts: Facts, out: Reconciliation, name: str, *, full_only: bool
) -> Iterator[tuple[GameFacts, int]]:
    """Yield (game, team) for a team-game check, keeping its denominators."""
    total = out.total(name, "team-game")
    for game in facts.games.values():
        if full_only and not game.is_full:
            total.not_checkable += 2
            continue
        for team in game.teams():
            total.checked += 1
            yield game, team


def _add(
    out: Reconciliation,
    check: str,
    game: GameFacts,
    team: int | None,
    expected: int,
    actual: int,
    detail: str,
    explanation: str = UNEXPLAINED,
) -> None:
    out.findings.append(
        Finding(
            check=check,
            explanation=explanation,
            detail=detail,
            season_id=game.season_id,
            game_id=game.game_id,
            team_id=team,
            expected=expected,
            actual=actual,
        )
    )


# --- score and goals ------------------------------------------------------------


def _score_matches_box(facts: Facts, out: Reconciliation) -> None:
    name = "score_matches_box"
    for game, team in _team_game_checks(facts, out, name, full_only=False):
        stats = facts.team_stats.get((game.game_id, team))
        box = stats.goals if stats else 0
        if box != game.goals_for(team):
            _add(out, name, game, team, game.goals_for(team), box,
                 "schedule score vs box-score team goals")


def _goal_events_match_score(facts: Facts, out: Reconciliation) -> None:
    name = "goal_events_match_score"
    for game, team in _team_game_checks(facts, out, name, full_only=False):
        expected = game.goals_for(team) - game.shootout_credit(team)
        actual = facts.goals.get((game.game_id, team), GoalTotals()).goals
        if actual != expected:
            _add(out, name, game, team, expected, actual,
                 "goal events vs schedule score (less any shootout credit)")


def _period_goals_sum(facts: Facts, out: Reconciliation) -> None:
    name = "period_goals_sum"
    for game, team in _team_game_checks(facts, out, name, full_only=False):
        hg, _, vg, _ = facts.periods.get(game.game_id, (0, 0, 0, 0))
        actual = hg if team == game.home_team_id else vg
        expected = game.goals_for(team) - game.shootout_credit(team)
        if actual != expected:
            _add(out, name, game, team, expected, actual,
                 "per-period goals vs schedule score (less any shootout credit)")


def _period_shots_sum(facts: Facts, out: Reconciliation) -> None:
    name = "period_shots_sum"
    for game, team in _team_game_checks(facts, out, name, full_only=True):
        _, hs, _, vs = facts.periods.get(game.game_id, (0, 0, 0, 0))
        actual = hs if team == game.home_team_id else vs
        stats = facts.team_stats.get((game.game_id, team))
        expected = stats.shots if stats else 0
        if actual != expected:
            # Some scorekeeping setups add the deciding shootout goal to the
            # winner's shot total (137 of 290 full shootout box scores).
            shootout_shot = game.shootout_credit(team) == 1 and expected - actual == 1
            _add(out, name, game, team, expected, actual, "per-period shots vs team shots",
                 SHOOTOUT_WINNER_SHOT if shootout_shot else UNEXPLAINED)


# --- goalies ----------------------------------------------------------------------


def _goalies_recorded(facts: Facts, out: Reconciliation) -> None:
    name = "goalies_recorded"
    for game, team in _team_game_checks(facts, out, name, full_only=True):
        if not facts.stints.get((game.game_id, team)):
            _add(out, name, game, team, 1, 0, "full box score with no goalie in net")


def _goalie_ga_matches(facts: Facts, out: Reconciliation) -> None:
    """Goals charged to a team's goalies vs goals the opponent scored.

    An empty-net goal is charged to no goalie, so a shortfall equal to the
    opponent's empty-net goals is explained, not a fault. So is one equal to the
    goals scored after the goalie's last stint ended, when the scorekeeper did
    not flag them empty-net.
    """
    name = "goalie_ga_matches"
    for game, team in _team_game_checks(facts, out, name, full_only=True):
        stints = facts.stints.get((game.game_id, team))
        if not stints:
            continue  # reported by goalies_recorded
        opponent = game.opponent(team)
        expected = game.goals_for(opponent) - game.shootout_credit(opponent)
        actual = sum(s.goals_against for s in stints)
        if actual == expected:
            continue
        shortfall = expected - actual
        empty_net = facts.goals.get((game.game_id, opponent), GoalTotals()).empty_net
        unflagged = [g for g in facts.goals_after_pull(game, team) if not g.is_empty_net]
        if empty_net and shortfall == empty_net:
            explanation = EMPTY_NET
        elif unflagged and shortfall == empty_net + len(unflagged):
            explanation = UNFLAGGED_EMPTY_NET
        else:
            explanation = UNEXPLAINED
        _add(out, name, game, team, expected, actual,
             f"goalie GA vs opponent goals; flagged empty-net: {empty_net}, "
             f"unflagged goals after the goalie left: {len(unflagged)}",
             explanation)


def _goalie_saves(facts: Facts, out: Reconciliation) -> None:
    name = "goalie_saves"
    total = out.total(name, "goalie-stint")
    for game in facts.games.values():
        if not game.is_full:
            continue
        for team in game.teams():
            for stint in facts.stints.get((game.game_id, team), []):
                total.checked += 1
                if stint.saves != stint.shots_against - stint.goals_against:
                    out.findings.append(Finding(
                        check=name, explanation=UNEXPLAINED, season_id=game.season_id,
                        game_id=game.game_id, team_id=team, player_id=stint.player_id,
                        expected=stint.shots_against - stint.goals_against, actual=stint.saves,
                        detail=f"saves vs SA {stint.shots_against} - GA {stint.goals_against}",
                    ))


# --- skaters and penalties -----------------------------------------------------------


def _skater_goals_match(facts: Facts, out: Reconciliation) -> None:
    name = "skater_goals_match"
    for game, team in _team_game_checks(facts, out, name, full_only=True):
        expected = facts.goals.get((game.game_id, team), GoalTotals()).with_scorer
        actual = facts.skaters.get((game.game_id, team), SkaterTotals()).goals
        if actual != expected:
            _add(out, name, game, team, expected, actual,
                 "skater-line goals vs goal events with a scorer")


def _assists_match(facts: Facts, out: Reconciliation) -> None:
    name = "assists_match"
    for game, team in _team_game_checks(facts, out, name, full_only=True):
        expected = facts.goals.get((game.game_id, team), GoalTotals()).assists
        actual = facts.skaters.get((game.game_id, team), SkaterTotals()).assists
        if actual != expected:
            # A goalie's assist sits in the goalie log, which has no skater line.
            by_goalie = facts.goalie_assists.get((game.game_id, team), 0)
            _add(out, name, game, team, expected, actual,
                 f"skater-line assists vs assists on goal events; by the goalie: {by_goalie}",
                 GOALIE_ASSIST if by_goalie and expected - actual == by_goalie
                 else UNEXPLAINED)


def _pim_matches(facts: Facts, out: Reconciliation) -> None:
    name = "pim_matches"
    for game, team in _team_game_checks(facts, out, name, full_only=True):
        stats = facts.team_stats.get((game.game_id, team))
        expected = stats.penalty_minutes if stats else 0
        actual = facts.penalty_minutes.get((game.game_id, team), 0)
        if actual != expected:
            _add(out, name, game, team, expected, actual,
                 "penalty-event minutes vs box-score team PIM")


def _pp_goals_within_opportunities(facts: Facts, out: Reconciliation) -> None:
    """More PP goals than PP opportunities is impossible.

    ...unless the field reads 0 although the opponent took minors or majors
    (it was simply not entered), or the excess is covered by the opponent's 4-
    and 5-minute penalties, which a goal does not end.
    """
    name = "pp_goals_within_opportunities"
    for game, team in _team_game_checks(facts, out, name, full_only=True):
        stats = facts.team_stats.get((game.game_id, team))
        if stats is None or stats.pp_goals <= stats.pp_opportunities:
            continue
        opponent = (game.game_id, game.opponent(team))
        opponent_minors = facts.advantage_penalties.get(opponent, 0)
        long_penalties = facts.long_penalties.get(opponent, 0)
        if stats.pp_opportunities == 0 and opponent_minors > 0:
            explanation = UNENTERED_ZERO
        elif stats.pp_goals - stats.pp_opportunities <= long_penalties:
            explanation = MAJOR_PENALTY
        else:
            explanation = UNEXPLAINED
        _add(out, name, game, team, stats.pp_opportunities, stats.pp_goals,
             f"PP goals vs PP opportunities; opponent minors/majors: {opponent_minors}, "
             f"of which 4/5-minute: {long_penalties}",
             explanation)


def _pp_goal_flags_match(facts: Facts, out: Reconciliation) -> None:
    name = "pp_goal_flags_match"
    for game, team in _team_game_checks(facts, out, name, full_only=True):
        stats = facts.team_stats.get((game.game_id, team))
        expected = stats.pp_goals if stats else 0
        actual = facts.goals.get((game.game_id, team), GoalTotals()).power_play
        if actual != expected:
            _add(out, name, game, team, expected, actual,
                 "goal events flagged power-play vs box-score PP goals")


def _player_on_roster(facts: Facts, out: Reconciliation) -> None:
    """Every player in an MD1 team's box scores should be on its roster that season.

    Only MD1 team-seasons are checkable: opponents' rosters were never fetched.
    """
    name = "player_on_roster"
    total = out.total(name, "player-team-season")
    on_league_line = {(u.season_id, u.team_id, u.player_id) for u in facts.upstream}
    for (season_id, team_id), players in sorted(facts.appearances.items()):
        if (season_id, team_id) not in facts.md1:
            total.not_checkable += len(players)
            continue
        roster = facts.rosters.get((season_id, team_id), set())
        for player_id in sorted(players):
            total.checked += 1
            if player_id not in roster:
                out.findings.append(Finding(
                    check=name,
                    explanation=ROSTER_FEED_OMISSION
                    if (season_id, team_id, player_id) in on_league_line
                    else UNEXPLAINED,
                    season_id=season_id, team_id=team_id, player_id=player_id,
                    detail="box-score player missing from the team's roster",
                ))


# --- the league's own season lines ------------------------------------------------------


def _box_goalie_seasons(facts: Facts) -> dict[tuple[int, int, int], tuple[int, int]]:
    """(season, team, goalie) -> (games with a stint, goals against), from box scores."""
    games: dict[tuple[int, int, int], set[int]] = defaultdict(set)
    ga: dict[tuple[int, int, int], int] = defaultdict(int)
    for (game_id, team_id), stints in facts.stints.items():
        game = facts.games.get(game_id)
        if game is None:
            continue
        for stint in stints:
            key = (game.season_id, team_id, stint.player_id)
            games[key].add(game_id)
            ga[key] += stint.goals_against
    return {key: (len(games[key]), ga[key]) for key in games}


def _upstream_goalies(facts: Facts, out: Reconciliation) -> None:
    lines = {
        (u.season_id, u.team_id, u.player_id): u for u in facts.upstream if u.kind == "goalie"
    }
    if not lines:
        return
    box = _box_goalie_seasons(facts)
    covered = {(s, t) for s, t, _ in lines}
    gp_total = out.total("upstream_goalie_gp", "goalie-season")
    ga_total = out.total("upstream_goalie_ga", "goalie-season")

    for key in sorted(set(lines) | {k for k in box if (k[0], k[1]) in covered}):
        season_id, team_id, player_id = key
        line = lines.get(key)
        box_gp, box_ga = box.get(key, (0, 0))
        if line is None:
            gp_total.checked += 1
            out.findings.append(Finding(
                check="upstream_goalie_gp", explanation=UNEXPLAINED, season_id=season_id,
                team_id=team_id, player_id=player_id, expected=box_gp, actual=None,
                detail="goalie in box scores but not in the league's season stats",
            ))
            continue
        upstream_gp, upstream_ga = line.games_played or 0, line.goals_against or 0
        if upstream_gp == 0 and box_gp == 0:
            continue  # dressed, never played: nothing to compare
        gp_total.checked += 1
        ga_total.checked += 1
        other_squad = line.division != facts.divisions.get((season_id, team_id), line.division)
        if box_gp == 0:
            explanation = UPSTREAM_ONLY_PLAYER
        elif other_squad:
            explanation = OTHER_SQUAD_GAMES
        else:
            explanation = UNEXPLAINED
        if upstream_gp != box_gp:
            out.findings.append(Finding(
                check="upstream_goalie_gp", explanation=explanation, season_id=season_id,
                team_id=team_id, player_id=player_id, expected=box_gp, actual=upstream_gp,
                detail=f"{line.name}: games in box scores vs league season line",
            ))
        if upstream_ga != box_ga:
            decider = (explanation == UNEXPLAINED and line.shootout_losses
                       and upstream_ga - box_ga == line.shootout_losses)
            out.findings.append(Finding(
                check="upstream_goalie_ga",
                explanation=SHOOTOUT_DECIDER if decider else explanation,
                season_id=season_id, team_id=team_id, player_id=player_id,
                expected=box_ga, actual=upstream_ga,
                detail=f"{line.name}: goals against in box scores vs league season line",
            ))


def _upstream_skaters(facts: Facts, out: Reconciliation) -> None:
    """Season G / A / PIM per skater: box-score lines vs the league's season line.

    Presence is its own check, so a player missing on one side is reported once
    rather than once per stat. A blank upstream value is compared as 0.
    """
    lines = {
        (u.season_id, u.team_id, u.player_id): u
        for u in facts.upstream
        if u.kind == "skater" and u.position != "G"
    }
    if not lines:
        return
    covered = {(s, t) for s, t, _ in lines}
    box = {k: v for k, v in facts.skater_seasons.items() if (k[0], k[1]) in covered}

    presence = out.total("upstream_skater_presence", "skater-season")
    for key in sorted(set(lines) | set(box)):
        season_id, team_id, player_id = key
        line, totals = lines.get(key), box.get(key)
        if line is not None and totals is not None:
            continue
        if line is not None and not any(
            (line.games_played, line.goals, line.assists, line.penalty_minutes)
        ):
            continue  # listed with nothing: rostered, never played
        presence.checked += 1
        if line is None:
            out.findings.append(Finding(
                check="upstream_skater_presence", explanation=UNEXPLAINED,
                season_id=season_id, team_id=team_id, player_id=player_id,
                detail="skater in box scores but not in the league's season stats",
            ))
        else:
            out.findings.append(Finding(
                check="upstream_skater_presence", explanation=UPSTREAM_ONLY_PLAYER,
                season_id=season_id, team_id=team_id, player_id=player_id,
                actual=line.games_played,
                detail=f"{line.name}: in the league's season stats, in no box score",
            ))

    for stat in ("goals", "assists", "penalty_minutes"):
        name = f"upstream_skater_{stat}"
        total = out.total(name, "skater-season")
        for key in sorted(set(lines) & set(box)):
            total.checked += 1
            expected = getattr(box[key], stat)
            line = lines[key]
            actual = getattr(line, stat) or 0
            if actual != expected:
                season_id, team_id, player_id = key
                if line.division != facts.divisions.get((season_id, team_id), line.division):
                    explanation = OTHER_SQUAD_GAMES
                elif (stat == "goals" and line.shootout_winning_goals
                      and actual - expected == line.shootout_winning_goals):
                    explanation = SHOOTOUT_DECIDER
                else:
                    explanation = UNEXPLAINED
                out.findings.append(Finding(
                    check=name, explanation=explanation, season_id=season_id,
                    team_id=team_id, player_id=player_id, expected=expected, actual=actual,
                    detail=f"{line.name}: {stat} in box scores vs league season line",
                ))
