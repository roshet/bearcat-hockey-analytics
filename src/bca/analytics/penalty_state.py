"""Reconstruct each game's strength state from its penalty log.

The league never publishes power-play or penalty-kill *time*, and its PP
opportunity field is entered in only 79% of team-games (M2). This rebuilds
both from the penalties and goals, second by second.

Pure: no database. A game's facts go in, its strength timeline comes out, so
the golden tests on hand-computed real games are exact.

What the feed's penalty minutes mean (verified across 68,914 penalties):

====  ==========================================  =================
min   what it is                                  man advantage
====  ==========================================  =================
2     minor (or bench minor)                      2:00
5     major                                       5:00
10    misconduct / game misconduct                none
12    minor + misconduct, one event               2:00
15    major + game misconduct or DQ, one event    5:00
====  ==========================================  =================

There are no 4-minute entries: a double minor is two 2-minute events for the
same player at the same instant, served back to back. A minor whose
description says "Penalty Shot" was awarded as a penalty shot instead of
being served.

The rules the ACHA's scorekeepers follow are not published anywhere we could
find, so the uncertain ones are :class:`Rules` options, chosen by calibrating
against the league's own data (``bca special-teams validate``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from bca.analytics.infractions import infraction_name

PERIOD_SECONDS = 1200
REGULATION_END = 3 * PERIOD_SECONDS
#: Regular-season overtime: every shootout game's goalie log ends OT at 5:00.
OVERTIME_SECONDS = 300
MINOR_SECONDS = 120
MAJOR_SECONDS = 300
#: A team never drops below three skaters; further penalties wait (delayed).
MAX_IN_BOX = 2


class Kind(Enum):
    MINOR = "minor"
    MAJOR = "major"


@dataclass(frozen=True)
class Rules:
    """The rule choices the data has to settle.

    ``coincidental_four_on_four``: coincidental minors at even strength put
    both teams a skater down (NHL) instead of leaving them at full strength
    with substitutes (USA Hockey, IIHF). Never a power play either way.

    ``opportunity``: ``per_penalty`` counts each penalty that sends the
    opponent short-handed; ``per_advantage`` only those that actually gave
    the other side a man advantage at some point (not one that only ever
    made it 4-on-4); ``per_episode`` counts each continuous spell of man
    advantage, however many penalties it took.

    ``double_minor_opportunities``: whether a double minor counts once or twice
    (``per_penalty`` only; ``per_advantage`` counts each half that was served
    on a man advantage, as the league's scorekeepers flag each half).

    Calibrated on 6,223 games (``bca special-teams validate``): every
    combination agrees with the scorekeepers' flags to within 1-2%, less than
    their own inconsistency, so the defaults are chosen on principle among the
    near-best: like-for-like cancellation (USA Hockey; and 3,001 of 3,432
    one-minor-each instants are unflagged on both sides), full strength for
    coincidentals, and an opportunity is a penalty that really gave the
    other side a man advantage.

    ``ot_skaters``: 5 or 3 a side in overtime. Only labels the strength; who
    has the advantage does not change.
    """

    coincidental_four_on_four: bool = False
    opportunity: str = "per_advantage"
    double_minor_opportunities: int = 1
    ot_skaters: int = 5
    #: ``like_for_like`` cancels majors against majors and minors against
    #: minors. ``by_time`` cancels penalty *time*: a minor against a major
    #: leaves the major's side short-handed for the 3:00 difference.
    coincidental: str = "like_for_like"
    #: One player's penalties from a single instant: ``concurrent`` boxes each
    #: in its own place (two men down for a major and a minor); ``consecutive``
    #: serves those left after cancellation back to back in one place, majors
    #: first, as double minors already are; ``deduplicated`` does the same but
    #: first drops a major that repeats one the player already has: the same
    #: infraction entered again (a 5 and a 15, "Major-Grab Face Mask" and "...
    #: w/Disqualification DQ"), or a "Disqualification w/Major" beside its
    #: major. A different second major (cross-checking and fighting) is real
    #: and still counts towards coincidental majors.
    same_player: str = "deduplicated"


@dataclass(frozen=True)
class Penalty:
    penalty_id: int
    #: Seconds since the opening faceoff (OT starts at 3600).
    time: int
    team_id: int
    minutes: int
    description: str
    #: Who served it: the player for a double-minor chain. None for unknown.
    player_id: int | None = None


@dataclass(frozen=True)
class Goal:
    goal_id: int
    time: int
    team_id: int
    is_empty_net: bool = False


@dataclass(frozen=True)
class GameInput:
    game_id: int
    home_team_id: int
    visiting_team_id: int
    penalties: tuple[Penalty, ...]
    goals: tuple[Goal, ...]
    #: When play ended: 3600, or 3600 + OT played.
    end_time: int = REGULATION_END

    def opponent(self, team_id: int) -> int:
        return self.visiting_team_id if team_id == self.home_team_id else self.home_team_id


def game_time(period: int, seconds: int) -> int:
    """Period + elapsed seconds -> seconds since the opening faceoff."""
    return (period - 1) * PERIOD_SECONDS + seconds


def classify(minutes: int, description: str) -> list[Kind]:
    """The parts of one penalty event that can create a man advantage."""
    if "penalty shot" in description.lower():
        return []
    parts = {2: [Kind.MINOR], 5: [Kind.MAJOR], 10: [], 12: [Kind.MINOR], 15: [Kind.MAJOR]}
    if minutes not in parts:
        raise ValueError(f"unrecognised penalty length {minutes} ({description!r})")
    return list(parts[minutes])


# --- output ---------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    home_skaters: int
    visiting_skaters: int


@dataclass
class TeamSpecialTeams:
    team_id: int
    pp_opportunities: int = 0
    pp_seconds: int = 0
    pk_seconds: int = 0
    pp_goals: int = 0
    sh_goals: int = 0
    pp_goals_against: int = 0
    sh_goals_against: int = 0


@dataclass(frozen=True)
class GoalStrength:
    goal_id: int
    team_id: int
    #: ``pp``, ``sh`` or ``ev`` for the scoring team, from skater counts alone.
    situation: str
    skaters_for: int
    skaters_against: int
    #: For a PP goal: the shorthanded team's penalties being served when it was
    #: scored (the current half of a double minor; never a coincidental).
    serving_penalty_ids: tuple[int, ...] = ()


@dataclass
class PenaltyOutcome:
    """What one penalty event did to the strength state."""

    penalty_id: int
    team_id: int
    #: Every part of it was cancelled by a coincidental penalty (or it has no
    #: part that can create an advantage: a misconduct, a penalty shot).
    cancelled: bool = True
    #: Seconds it was served while the opponent had more skaters.
    advantage_seconds: int = 0
    #: It had to wait for a box place (a third penalty).
    delayed: bool = False

    @property
    def created_advantage(self) -> bool:
        return self.advantage_seconds > 0


@dataclass
class GameStrength:
    game_id: int
    segments: list[Segment]
    teams: dict[int, TeamSpecialTeams]
    goals: list[GoalStrength]
    penalties: dict[int, PenaltyOutcome] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


# --- simulation -----------------------------------------------------------------


@dataclass
class _Serving:
    kind: Kind
    remaining: int
    #: Further minors of the same double-minor chain, served after this one.
    chained: int = 0
    #: A coincidental minor in four-on-four mode: reduces strength, never ended by a goal.
    coincidental: bool = False
    #: The penalty events this time belongs to, for per-penalty outcomes.
    penalty_ids: tuple[int, ...] = ()
    #: The same player's further penalties, served in this place after this one.
    after: list[_Serving] = field(default_factory=list)


@dataclass
class _Box:
    serving: list[_Serving] = field(default_factory=list)
    waiting: list[_Serving] = field(default_factory=list)

    def strength_loss(self) -> int:
        return len(self.serving)

    def admit(self, item: _Serving) -> bool:
        """Box a penalty; False when it has to wait for a place."""
        if len(self.serving) < MAX_IN_BOX:
            self.serving.append(item)
            return True
        self.waiting.append(item)
        return False

    def advance(self, seconds: int) -> None:
        for item in self.serving:
            item.remaining -= seconds

    def expire(self) -> None:
        """Retire finished penalties; start a chained minor or a waiting one."""
        still: list[_Serving] = []
        for item in self.serving:
            if item.remaining > 0:
                still.append(item)
            elif item.chained:
                still.append(_Serving(Kind.MINOR, MINOR_SECONDS + item.remaining,
                                      item.chained - 1, item.coincidental, item.penalty_ids,
                                      item.after))
            elif item.after:
                follow, *rest = item.after
                follow.remaining += item.remaining
                follow.after = rest
                still.append(follow)
        self.serving = still
        while self.waiting and len(self.serving) < MAX_IN_BOX:
            self.serving.append(self.waiting.pop(0))

    def serving_now(self) -> tuple[int, ...]:
        """The penalty events being served right now, one per box place.

        A chain holds every half's event id in order; the one being served is
        the half after those already done (``chained`` halves are still to come).
        """
        ids: list[int] = []
        for item in self.serving:
            if item.coincidental or not item.penalty_ids:
                continue
            index = max(0, len(item.penalty_ids) - 1 - item.chained)
            ids.append(item.penalty_ids[index])
        return tuple(ids)

    def next_expiry(self) -> int | None:
        return min((item.remaining for item in self.serving), default=None)

    def end_one_minor(self) -> bool:
        """A power-play goal against: the minor closest to expiring ends."""
        minors = [i for i in self.serving if i.kind is Kind.MINOR and not i.coincidental]
        if not minors:
            return False
        first = min(minors, key=lambda i: i.remaining)
        first.remaining = 0
        self.expire()
        return True


@dataclass
class _Chain:
    """A run of minors for one player (one half for a single minor)."""

    halves: int
    penalty_ids: tuple[int, ...]
    player_id: int | None = None


@dataclass
class _Major:
    seconds: int
    penalty_ids: tuple[int, ...]
    player_id: int | None = None


@dataclass
class _Called:
    """One team's strength-affecting penalties called at a single instant."""

    majors: list[_Major] = field(default_factory=list)
    minor_chains: list[_Chain] = field(default_factory=list)

    def minors(self) -> int:
        return sum(c.halves for c in self.minor_chains)

    def cancel_minors(self, n: int) -> None:
        """Remove ``n`` minor halves, single minors first, then from chains.

        A half cancelled out of a chain takes one of its penalty events with
        it, so the advantage is credited only to the minors actually served.
        """
        self.minor_chains.sort(key=lambda c: c.halves)
        while n:
            chain = self.minor_chains[0]
            chain.halves -= 1
            chain.penalty_ids = chain.penalty_ids[:-1]
            if chain.halves == 0:
                self.minor_chains.pop(0)
            n -= 1


#: A major whose description only says it carried a disqualification: the DQ
#: of the player's other major at that instant, not an infraction of its own.
_DQ_WITH_MAJOR = "Disqualification (major)"


def _repeats(major: Penalty, earlier: list[Penalty]) -> bool:
    """``major`` is the same player's same major entered again at this instant."""
    name = infraction_name(major.minutes, major.description)
    return any(
        p.player_id == major.player_id
        and (name == _DQ_WITH_MAJOR
             or infraction_name(p.minutes, p.description) in (name, _DQ_WITH_MAJOR))
        for p in earlier
    )


def called_at(penalties: list[Penalty], team_id: int, deduplicate: bool = False) -> _Called:
    called = _Called()
    per_player: dict[int, list[int]] = {}
    majors: list[Penalty] = []
    for p in penalties:
        if p.team_id != team_id:
            continue
        for kind in classify(p.minutes, p.description):
            if kind is Kind.MAJOR:
                if deduplicate and p.player_id is not None and _repeats(p, majors):
                    continue
                majors.append(p)
                called.majors.append(_Major(MAJOR_SECONDS, (p.penalty_id,), p.player_id))
            elif p.player_id is None:
                called.minor_chains.append(_Chain(1, (p.penalty_id,)))
            else:
                per_player.setdefault(p.player_id, []).append(p.penalty_id)
    called.minor_chains += [
        _Chain(len(ids), tuple(ids), player) for player, ids in per_player.items()
    ]
    called.minor_chains.sort(key=lambda c: c.halves)
    return called


def simulate(game: GameInput, rules: Rules | None = None) -> GameStrength:
    return _Simulation(game, rules or Rules()).run()


class _Simulation:
    def __init__(self, game: GameInput, rules: Rules) -> None:
        self.game = game
        self.rules = rules
        self.teams = (game.home_team_id, game.visiting_team_id)
        self.boxes = {team: _Box() for team in self.teams}
        self.out = {team: TeamSpecialTeams(team) for team in self.teams}
        self.segments: list[Segment] = []
        self.goal_strengths: list[GoalStrength] = []
        self.issues: list[str] = []
        self.in_advantage = {team: False for team in self.teams}
        self.outcomes: dict[int, PenaltyOutcome] = {}
        self.now = 0

    # -- state ---------------------------------------------------------------------

    def skaters(self, team: int) -> int:
        base = self.rules.ot_skaters if self.now >= REGULATION_END else 5
        own = self.boxes[team].strength_loss()
        other = self.boxes[self.game.opponent(team)].strength_loss()
        if base == 5:
            return 5 - own
        # 3-on-3 overtime: a penalty adds a skater to the other side instead.
        return min(5, base + max(0, other - own))

    # -- driver --------------------------------------------------------------------

    def run(self) -> GameStrength:
        game = self.game
        penalties_at: dict[int, list[Penalty]] = {}
        for p in game.penalties:
            if p.time > game.end_time:
                self.issues.append(
                    f"penalty {p.penalty_id} at {p.time}s is after play ended ({game.end_time}s)"
                )
            else:
                penalties_at.setdefault(p.time, []).append(p)
        goals = sorted(
            (g for g in game.goals if g.time <= game.end_time), key=lambda g: (g.time, g.goal_id)
        )
        for g in game.goals:
            if g.time > game.end_time:
                self.issues.append(
                    f"goal {g.goal_id} at {g.time}s is after play ended ({game.end_time}s)"
                )

        instants = sorted(penalties_at)
        goal_index = instant_index = 0
        while True:
            candidates = [game.end_time]
            if self.now < REGULATION_END < game.end_time:
                candidates.append(REGULATION_END)  # overtime may change the skater count
            expiries = [e for e in (b.next_expiry() for b in self.boxes.values()) if e is not None]
            if expiries:
                candidates.append(self.now + min(expiries))
            if goal_index < len(goals):
                candidates.append(goals[goal_index].time)
            if instant_index < len(instants):
                candidates.append(instants[instant_index])
            self._advance_to(min(candidates))

            # At one instant: expiries, then goals, then newly called penalties
            # (a penalty called on a goal's whistle starts after it).
            for box in self.boxes.values():
                box.expire()
            self._note_advantage()
            while goal_index < len(goals) and goals[goal_index].time == self.now:
                self._score(goals[goal_index])
                goal_index += 1
            if instant_index < len(instants) and instants[instant_index] == self.now:
                self._assess(penalties_at[self.now])
                instant_index += 1
            if self.now >= game.end_time:
                break

        if self.rules.opportunity == "per_advantage":
            for team in self.teams:
                self.out[team].pp_opportunities = sum(
                    o.created_advantage for o in self.outcomes.values()
                    if o.team_id == game.opponent(team)
                )
        return GameStrength(game.game_id, self.segments, self.out, self.goal_strengths,
                            self.outcomes, self.issues)

    def _advance_to(self, until: int) -> None:
        if until <= self.now:
            return
        home, away = self.skaters(self.game.home_team_id), self.skaters(self.game.visiting_team_id)
        last = self.segments[-1] if self.segments else None
        if last and (last.home_skaters, last.visiting_skaters) == (home, away):
            self.segments[-1] = Segment(last.start, until, home, away)
        else:
            self.segments.append(Segment(self.now, until, home, away))
        elapsed = until - self.now
        if home != away:
            ahead, behind = (
                (self.game.home_team_id, self.game.visiting_team_id) if home > away
                else (self.game.visiting_team_id, self.game.home_team_id)
            )
            self.out[ahead].pp_seconds += elapsed
            self.out[behind].pk_seconds += elapsed
            for item in self.boxes[behind].serving:
                for penalty_id in item.penalty_ids:
                    self.outcomes[penalty_id].advantage_seconds += elapsed
        for box in self.boxes.values():
            box.advance(elapsed)
        self.now = until

    def _note_advantage(self) -> None:
        for team in self.teams:
            ahead = self.skaters(team) > self.skaters(self.game.opponent(team))
            if ahead and not self.in_advantage[team] and self.rules.opportunity == "per_episode":
                self.out[team].pp_opportunities += 1
            self.in_advantage[team] = ahead

    # -- events --------------------------------------------------------------------

    def _score(self, goal: Goal) -> None:
        team, opponent = goal.team_id, self.game.opponent(goal.team_id)
        mine, theirs = self.skaters(team), self.skaters(opponent)
        situation = "pp" if mine > theirs else "sh" if mine < theirs else "ev"
        serving = self.boxes[opponent].serving_now() if situation == "pp" else ()
        self.goal_strengths.append(
            GoalStrength(goal.goal_id, team, situation, mine, theirs, serving)
        )
        if situation == "pp":
            self.out[team].pp_goals += 1
            self.out[opponent].pp_goals_against += 1
            self.boxes[opponent].end_one_minor()
            self._note_advantage()
        elif situation == "sh":
            self.out[team].sh_goals += 1
            self.out[opponent].sh_goals_against += 1

    def _assess(self, penalties: list[Penalty]) -> None:
        """Every penalty called at one instant: cancel coincidentals, box the rest."""
        for p in penalties:
            self.outcomes[p.penalty_id] = PenaltyOutcome(p.penalty_id, p.team_id)
        deduplicate = self.rules.same_player == "deduplicated"
        home = called_at(penalties, self.game.home_team_id, deduplicate)
        away = called_at(penalties, self.game.visiting_team_id, deduplicate)

        majors = min(len(home.majors), len(away.majors))
        del home.majors[:majors], away.majors[:majors]
        minors = min(home.minors(), away.minors())
        home.cancel_minors(minors)
        away.cancel_minors(minors)
        if self.rules.coincidental == "by_time":
            # Each minor left on one side cancels 2:00 of a major on the other.
            for minor_side, major_side in ((home, away), (away, home)):
                while minor_side.minor_chains and major_side.majors:
                    minor_side.cancel_minors(1)
                    major = major_side.majors[0]
                    major.seconds -= MINOR_SECONDS
                    if major.seconds <= 0:
                        major_side.majors.pop(0)

        at_even = all(not box.serving for box in self.boxes.values())
        if self.rules.coincidental_four_on_four and minors == 1 and majors == 0 and at_even:
            for team in self.teams:
                self.boxes[team].admit(_Serving(Kind.MINOR, MINOR_SECONDS, coincidental=True))

        per_penalty = self.rules.opportunity == "per_penalty"
        for team, called in ((self.game.home_team_id, home), (self.game.visiting_team_id, away)):
            opponent = self.game.opponent(team)
            if per_penalty:
                self.out[opponent].pp_opportunities += len(called.majors) + sum(
                    self.rules.double_minor_opportunities if chain.halves > 1 else 1
                    for chain in called.minor_chains
                )
            for item in self._places(called):
                self._box(team, item)
        self._note_advantage()

    def _places(self, called: _Called) -> list[_Serving]:
        """One box place per penalty, or per player when one player's are served back to
        back: majors first, then the minor chain, each starting as the last one ends."""
        places: list[list[_Serving]] = []
        by_player: dict[int, list[_Serving]] = {}
        items = [(m.player_id, _Serving(Kind.MAJOR, m.seconds, penalty_ids=m.penalty_ids))
                 for m in called.majors]
        items += [(c.player_id, _Serving(Kind.MINOR, MINOR_SECONDS, chained=c.halves - 1,
                                         penalty_ids=c.penalty_ids))
                  for c in called.minor_chains]
        for player, item in items:
            if self.rules.same_player == "concurrent" or player is None:
                places.append([item])
            elif player in by_player:
                by_player[player].append(item)
            else:
                by_player[player] = [item]
                places.append(by_player[player])
        for first, *rest in places:
            first.after = rest
        return [first for first, *_ in places]

    def _box(self, team: int, item: _Serving) -> None:
        started = self.boxes[team].admit(item)
        for part in (item, *item.after):
            for penalty_id in part.penalty_ids:
                outcome = self.outcomes[penalty_id]
                outcome.cancelled = False
                outcome.delayed = outcome.delayed or not started
