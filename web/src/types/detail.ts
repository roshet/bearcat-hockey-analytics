/* Generated from the Pydantic TeamDetailReport model by npm run gen:types. Do not edit. */

export type TeamId = number;
export type Team = string;
export type Division = string;
export type BuiltAt = string;
export type PlayersAsNumbers = boolean;
export type SeasonId = number;
export type Season = string;
export type Games = number;
export type Games1 = number;
export type PpGoals = number;
export type PpOpportunities = number;
export type PpPct = number | null;
export type PpRange = [unknown, unknown] | null;
export type PkOpportunities = number;
export type PpGoalsAgainst = number;
export type PkPct = number | null;
export type PkRange = [unknown, unknown] | null;
export type PpMinutesPerGame = number | null;
export type ShMinutesPerGame = number | null;
export type ShGoalsFor = number;
export type ShGoalsAgainst = number;
export type LeaguePpOpportunities = number;
export type GamesNotEntered = number;
export type GamesCountedDifferently = number;
export type GameId = number;
export type Date = string | null;
export type Home = boolean;
export type Opponent = string;
export type Gf = number;
export type Ga = number;
export type Result = string;
export type Simulated = boolean;
export type PpGoals1 = number;
export type PpOpportunities1 = number;
export type PpMinutes = number;
export type PpGoalsAgainst1 = number;
export type PkOpportunities1 = number;
export type PkMinutes = number;
export type Minors = number;
export type Flag = string | null;
export type GameLog = GameLogRow[];
export type GameId1 = number;
export type Date1 = string | null;
export type Opponent1 = string;
export type Period = string;
export type Clock = string;
export type Strength = string;
export type Infraction = string;
export type Player = string;
export type Serving = ServedPenalty[];
export type PpGoalsAgainst2 = PowerPlayGoalAgainst[];
export type GameId2 = number;
export type Date2 = string | null;
export type Opponent2 = string;
export type Period1 = string;
export type Clock1 = string;
export type ScoredByTeam = boolean;
export type ShGoals = ShortHandedGoal[];
export type MinorsPerGame = number | null;
export type Infraction1 = string;
export type Count = number;
export type PerGame = number;
export type LeaguePerGame = number;
export type Infractions = InfractionCount[];
export type OtherInfractions = number;
export type Player1 = string;
export type Penalties = number;
export type Minors1 = number;
export type Pim = number;
export type ShMinutesCaused = number;
export type Players = PlayerDiscipline[];
export type Metric = string;
export type Value = number | null;
export type Rank = number | null;
export type Of = number;
export type HigherIsBetter = boolean;
export type Ranks = LeagueRank[];

export interface TeamDetailReport {
  team_id: TeamId;
  team: Team;
  division: Division;
  built_at: BuiltAt;
  players_as_numbers: PlayersAsNumbers;
  current: DetailSeason;
  prior: DetailSeason | null;
}
export interface DetailSeason {
  season_id: SeasonId;
  season: Season;
  games: Games;
  summary: SpecialTeamsSummary;
  game_log: GameLog;
  pp_goals_against: PpGoalsAgainst2;
  sh_goals: ShGoals;
  minors_per_game: MinorsPerGame;
  infractions: Infractions;
  other_infractions: OtherInfractions;
  players: Players;
  ranks: Ranks;
}
export interface SpecialTeamsSummary {
  games: Games1;
  pp_goals: PpGoals;
  pp_opportunities: PpOpportunities;
  pp_pct: PpPct;
  pp_range: PpRange;
  pk_opportunities: PkOpportunities;
  pp_goals_against: PpGoalsAgainst;
  pk_pct: PkPct;
  pk_range: PkRange;
  pp_minutes_per_game: PpMinutesPerGame;
  sh_minutes_per_game: ShMinutesPerGame;
  sh_goals_for: ShGoalsFor;
  sh_goals_against: ShGoalsAgainst;
  league_pp_opportunities: LeaguePpOpportunities;
  games_not_entered: GamesNotEntered;
  games_counted_differently: GamesCountedDifferently;
}
export interface GameLogRow {
  game_id: GameId;
  date: Date;
  home: Home;
  opponent: Opponent;
  gf: Gf;
  ga: Ga;
  result: Result;
  simulated: Simulated;
  pp_goals: PpGoals1;
  pp_opportunities: PpOpportunities1;
  pp_minutes: PpMinutes;
  pp_goals_against: PpGoalsAgainst1;
  pk_opportunities: PkOpportunities1;
  pk_minutes: PkMinutes;
  minors: Minors;
  flag: Flag;
}
export interface PowerPlayGoalAgainst {
  game_id: GameId1;
  date: Date1;
  opponent: Opponent1;
  period: Period;
  clock: Clock;
  strength: Strength;
  serving: Serving;
}
export interface ServedPenalty {
  infraction: Infraction;
  player: Player;
}
export interface ShortHandedGoal {
  game_id: GameId2;
  date: Date2;
  opponent: Opponent2;
  period: Period1;
  clock: Clock1;
  scored_by_team: ScoredByTeam;
}
export interface InfractionCount {
  infraction: Infraction1;
  count: Count;
  per_game: PerGame;
  league_per_game: LeaguePerGame;
}
export interface PlayerDiscipline {
  player: Player1;
  penalties: Penalties;
  minors: Minors1;
  pim: Pim;
  sh_minutes_caused: ShMinutesCaused;
}
export interface LeagueRank {
  metric: Metric;
  value: Value;
  rank: Rank;
  of: Of;
  higher_is_better: HigherIsBetter;
}
