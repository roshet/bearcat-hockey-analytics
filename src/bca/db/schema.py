"""The normalized schema.

Three rules shape it:

- **Feed ids are primary keys** wherever the feed provides one, so re-ingesting
  is an upsert and every row traces back to its source resource.
- **Upstream values are stored as reported.** ``game_team_stats`` keeps the
  scorekeeper's ``pp_opportunities`` even when it is an unentered ``0``;
  deciding what that means is the reconciliation suite's job (M2), not the
  loader's.
- **Team and player references carry no foreign keys.** A box score can name a
  player who is on no roster -- that is a real integrity gap in the data, and
  M2 must *report* it. A constraint would instead make the game un-loadable.
  Event tables do reference ``games`` (with cascade) so a game re-normalizes as
  a clean delete-and-reinsert.

Only portable column types are used so the test suite can run on SQLite.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _game_fk() -> Mapped[int]:
    return mapped_column(ForeignKey("games.game_id", ondelete="CASCADE"), primary_key=True)


# --- reference data ----------------------------------------------------------


class Season(Base):
    __tablename__ = "seasons"

    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str] = mapped_column(String(50))
    is_playoff: Mapped[bool] = mapped_column(Boolean)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)


class Team(Base):
    """A program, identified across seasons. Names reflect the latest season seen."""

    __tablename__ = "teams"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200))
    nickname: Mapped[str] = mapped_column(String(100))
    code: Mapped[str] = mapped_column(String(50))


class TeamSeason(Base):
    """A team's division membership in one season. UC moved MD2 -> MD1 in 2025-26."""

    __tablename__ = "team_seasons"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200))
    division_id: Mapped[int | None] = mapped_column(Integer)
    division_name: Mapped[str] = mapped_column(String(200))
    is_md1: Mapped[bool] = mapped_column(Boolean, index=True)


class Player(Base):
    """A person who played. Birthdates are deliberately not stored."""

    __tablename__ = "players"

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    position: Mapped[str] = mapped_column(String(10))


class RosterEntry(Base):
    __tablename__ = "roster_entries"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    jersey_number: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[str] = mapped_column(String(10))
    shoots: Mapped[str] = mapped_column(String(5))
    catches: Mapped[str] = mapped_column(String(5))
    active: Mapped[bool] = mapped_column(Boolean)


class StaffEntry(Base):
    __tablename__ = "staff_entries"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    person_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    role: Mapped[str] = mapped_column(String(100), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))


# --- games -------------------------------------------------------------------


class Game(Base):
    """One scheduled game. The schedule row is the source of truth for results."""

    __tablename__ = "games"

    game_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    season_id: Mapped[int] = mapped_column(Integer, index=True)
    game_date: Mapped[date | None] = mapped_column(Date)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    home_team_id: Mapped[int] = mapped_column(Integer, index=True)
    visiting_team_id: Mapped[int] = mapped_column(Integer, index=True)
    home_goals: Mapped[int] = mapped_column(Integer)
    visiting_goals: Mapped[int] = mapped_column(Integer)
    is_final: Mapped[bool] = mapped_column(Boolean)
    overtime: Mapped[bool] = mapped_column(Boolean)
    shootout: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(50))
    venue: Mapped[str] = mapped_column(String(200))
    #: NULL when not entered; the feed spells that ``""`` or ``0``.
    attendance: Mapped[int | None] = mapped_column(Integer)
    #: True once the box score has been parsed into the event tables.
    summary_normalized: Mapped[bool] = mapped_column(Boolean, default=False)
    #: ``full`` or ``score_only`` once the box score is loaded; NULL before.
    #: A score-only box score's zeros mean "not entered" -- see docs/data-sources.md.
    box_score_kind: Mapped[str | None] = mapped_column(String(20), index=True)


class GameTeamStats(Base):
    """Box-score team totals, exactly as the scorekeeper entered them."""

    __tablename__ = "game_team_stats"

    game_id: Mapped[int] = _game_fk()
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    is_home: Mapped[bool] = mapped_column(Boolean)
    shots: Mapped[int] = mapped_column(Integer)
    goals: Mapped[int] = mapped_column(Integer)
    pp_goals: Mapped[int] = mapped_column(Integer)
    pp_opportunities: Mapped[int] = mapped_column(Integer)
    penalty_minutes: Mapped[int] = mapped_column(Integer)
    infractions: Mapped[int] = mapped_column(Integer)


class PeriodStats(Base):
    __tablename__ = "period_stats"

    game_id: Mapped[int] = _game_fk()
    period: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    home_goals: Mapped[int] = mapped_column(Integer)
    home_shots: Mapped[int] = mapped_column(Integer)
    visiting_goals: Mapped[int] = mapped_column(Integer)
    visiting_shots: Mapped[int] = mapped_column(Integer)


class GoalEvent(Base):
    __tablename__ = "goal_events"

    game_id: Mapped[int] = _game_fk()
    goal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    period: Mapped[int] = mapped_column(Integer)
    #: Seconds elapsed in the period. NULL when the scorekeeper entered none.
    seconds: Mapped[int | None] = mapped_column(Integer)
    #: NULL for a score-only box score, where goals carry no scorer.
    scorer_id: Mapped[int | None] = mapped_column(Integer, index=True)
    is_power_play: Mapped[bool] = mapped_column(Boolean)
    is_short_handed: Mapped[bool] = mapped_column(Boolean)
    is_empty_net: Mapped[bool] = mapped_column(Boolean)
    is_penalty_shot: Mapped[bool] = mapped_column(Boolean)
    is_game_winning: Mapped[bool] = mapped_column(Boolean)


class GoalAssist(Base):
    __tablename__ = "goal_assists"

    game_id: Mapped[int] = _game_fk()
    goal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    #: 1 = primary assist, 2 = secondary.
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    player_id: Mapped[int] = mapped_column(Integer, index=True)


class PenaltyEvent(Base):
    __tablename__ = "penalty_events"

    game_id: Mapped[int] = _game_fk()
    penalty_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    period: Mapped[int] = mapped_column(Integer)
    #: Seconds elapsed in the period.
    seconds: Mapped[int] = mapped_column(Integer)
    minutes: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(200))
    rule_number: Mapped[str] = mapped_column(String(20))
    #: NULL for bench penalties.
    taken_by_id: Mapped[int | None] = mapped_column(Integer, index=True)
    served_by_id: Mapped[int | None] = mapped_column(Integer)
    is_bench: Mapped[bool] = mapped_column(Boolean)
    #: The scorekeeper's flag. A hint, not truth -- see docs/data-sources.md.
    upstream_is_power_play: Mapped[bool] = mapped_column(Boolean)


class GoalieAppearance(Base):
    """One stint in net, from the box score's ``goalieLog``."""

    __tablename__ = "goalie_appearances"

    game_id: Mapped[int] = _game_fk()
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    player_id: Mapped[int] = mapped_column(Integer, index=True)
    period_in: Mapped[int] = mapped_column(Integer)
    seconds_in: Mapped[int] = mapped_column(Integer)
    period_out: Mapped[int] = mapped_column(Integer)
    seconds_out: Mapped[int] = mapped_column(Integer)
    toi_seconds: Mapped[int | None] = mapped_column(Integer)
    shots_against: Mapped[int] = mapped_column(Integer)
    goals_against: Mapped[int] = mapped_column(Integer)
    saves: Mapped[int] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String(10))


class SkaterGameStats(Base):
    """Per-game skater line. Only the fields the ACHA actually records."""

    __tablename__ = "skater_game_stats"

    game_id: Mapped[int] = _game_fk()
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    jersey_number: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[str] = mapped_column(String(10))
    goals: Mapped[int] = mapped_column(Integer)
    assists: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    penalty_minutes: Mapped[int] = mapped_column(Integer)
    starting: Mapped[bool] = mapped_column(Boolean)


# --- upstream aggregates (compared against, never a source) -----------------


class UpstreamPlayerSeasonStats(Base):
    """The league's own season line for a player on a team.

    Convention 1: these are never used to compute anything. They exist so the
    audit can show where the league's summaries disagree with its game rows.
    Values are as published; NULL where the feed left a field blank.
    """

    __tablename__ = "upstream_player_season_stats"

    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    #: ``skater`` or ``goalie`` -- which stats view the line came from.
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    position: Mapped[str] = mapped_column(String(10))
    #: The division the league files the line under; see statview.py.
    division: Mapped[str] = mapped_column(String(200), server_default="")
    games_played: Mapped[int | None] = mapped_column(Integer)
    goals: Mapped[int | None] = mapped_column(Integer)
    assists: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[int | None] = mapped_column(Integer)
    penalty_minutes: Mapped[int | None] = mapped_column(Integer)
    shootout_winning_goals: Mapped[int | None] = mapped_column(Integer)
    goals_against: Mapped[int | None] = mapped_column(Integer)
    saves: Mapped[int | None] = mapped_column(Integer)
    minutes_played: Mapped[int | None] = mapped_column(Integer)
    shootout_losses: Mapped[int | None] = mapped_column(Integer)


# --- audit (M2) ---------------------------------------------------------------


class ReconciliationFinding(Base):
    """One place where the data disagrees with itself or with the league.

    Rebuilt wholesale by ``bca audit run``. ``explanation`` names a known cause,
    or is ``unexplained`` -- the number the M2 gate rests on.
    """

    __tablename__ = "reconciliation_findings"

    finding_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    check_name: Mapped[str] = mapped_column(String(50), index=True)
    season_id: Mapped[int | None] = mapped_column(Integer, index=True)
    game_id: Mapped[int | None] = mapped_column(Integer, index=True)
    team_id: Mapped[int | None] = mapped_column(Integer, index=True)
    player_id: Mapped[int | None] = mapped_column(Integer)
    expected: Mapped[int | None] = mapped_column(Integer)
    actual: Mapped[int | None] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(String(50), index=True)
    detail: Mapped[str] = mapped_column(Text)


class AuditCheckTotal(Base):
    """Denominators for each check, so a finding count can become a rate."""

    __tablename__ = "audit_check_totals"

    check_name: Mapped[str] = mapped_column(String(50), primary_key=True)
    #: What one unit is: ``team-game``, ``game``, ``goalie-season`` ...
    unit: Mapped[str] = mapped_column(String(30))
    checked: Mapped[int] = mapped_column(Integer)
    #: Units the check cannot apply to (e.g. score-only box scores).
    not_checkable: Mapped[int] = mapped_column(Integer)


class GameQuality(Base):
    """Which optional parts of one team's box score were actually entered.

    The UI's per-game data-quality flags. NULL means it cannot be told: a
    recorded 0 PP opportunities with no opponent minors or majors is plausible,
    and so is a one-goal game with no assist.
    """

    __tablename__ = "game_quality"

    game_id: Mapped[int] = _game_fk()
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    is_home: Mapped[bool] = mapped_column(Boolean)
    lineup: Mapped[bool] = mapped_column(Boolean)
    #: Every goal on the scoreboard has a goal event. False for a partly
    #: entered box score, where all event-based numbers are incomplete.
    goal_events: Mapped[bool] = mapped_column(Boolean, server_default=true())
    goal_times: Mapped[bool] = mapped_column(Boolean)
    assists: Mapped[bool | None] = mapped_column(Boolean)
    team_shots: Mapped[bool] = mapped_column(Boolean)
    period_shots: Mapped[bool] = mapped_column(Boolean)
    penalties: Mapped[bool] = mapped_column(Boolean)
    pp_opportunities: Mapped[bool | None] = mapped_column(Boolean)
    goalies: Mapped[bool] = mapped_column(Boolean)
    attendance: Mapped[bool] = mapped_column(Boolean)


# --- special teams (M3), derived by the penalty-state simulator -------------


class TeamGameSpecialTeams(Base):
    """Derived power-play and penalty-kill numbers for one team in one game.

    Rebuilt by ``bca special-teams run``; see ``analytics/penalty_state.py``.
    ``entered_pp_opportunities`` is the scorekeeper's field, kept beside the
    derived count for comparison -- NULL where game_quality says not entered.
    """

    __tablename__ = "team_game_special_teams"

    game_id: Mapped[int] = _game_fk()
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    pp_opportunities: Mapped[int] = mapped_column(Integer)
    pp_seconds: Mapped[int] = mapped_column(Integer)
    pk_seconds: Mapped[int] = mapped_column(Integer)
    pp_goals: Mapped[int] = mapped_column(Integer)
    sh_goals: Mapped[int] = mapped_column(Integer)
    pp_goals_against: Mapped[int] = mapped_column(Integer)
    sh_goals_against: Mapped[int] = mapped_column(Integer)
    entered_pp_opportunities: Mapped[int | None] = mapped_column(Integer)


class GoalStrength(Base):
    """The derived strength each goal was scored at, beside the scorekeeper's flags."""

    __tablename__ = "goal_strength"

    game_id: Mapped[int] = _game_fk()
    goal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    #: ``pp``, ``sh`` or ``ev`` for the scoring team.
    situation: Mapped[str] = mapped_column(String(2))
    skaters_for: Mapped[int] = mapped_column(Integer)
    skaters_against: Mapped[int] = mapped_column(Integer)
    #: The scorekeeper's flag, as ``pp`` / ``sh`` / ``ev``.
    flagged_situation: Mapped[str] = mapped_column(String(2))


class GoalStrengthPenalty(Base):
    """A penalty being served when a power-play goal was scored against its team."""

    __tablename__ = "goal_strength_penalties"

    game_id: Mapped[int] = _game_fk()
    goal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    penalty_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class PenaltyOutcome(Base):
    """What each penalty actually did: cancelled, delayed, and advantage given."""

    __tablename__ = "penalty_outcomes"

    game_id: Mapped[int] = _game_fk()
    penalty_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    delayed: Mapped[bool] = mapped_column(Boolean)
    #: Seconds served while the opponent had more skaters.
    advantage_seconds: Mapped[int] = mapped_column(Integer)
    flagged_power_play: Mapped[bool] = mapped_column(Boolean)


# --- scouting reports (M4), precomputed ---------------------------------------


class StoredScoutingReport(Base):
    """One team's built scouting report, as the JSON the API serves verbatim.

    Built by ``bca scouting build`` (``analytics/scouting.py``); the API never
    computes, it only reads this table.
    """

    __tablename__ = "scouting_reports"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team: Mapped[str] = mapped_column(String(200))
    division: Mapped[str] = mapped_column(String(200))
    payload: Mapped[str] = mapped_column(Text)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StoredTeamDetailReport(Base):
    """One team's special teams & discipline detail (M5), served verbatim."""

    __tablename__ = "team_detail_reports"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team: Mapped[str] = mapped_column(String(200))
    division: Mapped[str] = mapped_column(String(200))
    payload: Mapped[str] = mapped_column(Text)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StoredRankingsReport(Base):
    """One season's "Where we stand" page (M6), served verbatim."""

    __tablename__ = "rankings_reports"

    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    payload: Mapped[str] = mapped_column(Text)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublishedRanking(Base):
    """One team's row in an official ACHA M1 computer ranking PDF (M6).

    Parsed from the archived PDF (``data/cache/acha_rankings/``); kept only to
    check our rebuilt ranking against, never as a source for it.
    """

    __tablename__ = "published_rankings"

    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    rank: Mapped[int] = mapped_column(Integer)
    rank_a: Mapped[int] = mapped_column(Integer)
    rank_b: Mapped[int] = mapped_column(Integer)
    avg: Mapped[float] = mapped_column(Float)
    league: Mapped[str] = mapped_column(String(50))
    source_file: Mapped[str] = mapped_column(String(200))


# --- ingestion bookkeeping ---------------------------------------------------


class IngestIssue(Base):
    """Something that could not be loaded, or that broke an assumption.

    ``stage`` is ``parse`` / ``load`` for failures and ``canary`` for a
    never-recorded field turning up non-zero. This table is M2's first input.
    """

    __tablename__ = "ingest_issues"

    issue_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource: Mapped[str] = mapped_column(String(200), index=True)
    game_id: Mapped[int | None] = mapped_column(Integer, index=True)
    stage: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PipelineRun(Base):
    """One run of ``bca nightly`` (M7): how far it got, and whether it deployed."""

    __tablename__ = "pipeline_runs"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: running, success, skipped (nothing changed), failed, canary (stopped on purpose).
    status: Mapped[str] = mapped_column(String(20))
    #: The last step reached (bca.nightly.STEPS).
    step: Mapped[str] = mapped_column(String(20))
    deployed: Mapped[bool] = mapped_column(Boolean, default=False)
    export_digest: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
