/* Generated from the Pydantic ScoutingReport model by npm run gen:types. Do not edit. */

export type TeamId = number;
export type Team = string;
export type Division = string;
export type BuiltAt = string;
export type SeasonId = number;
export type Season = string;
export type Gp = number;
export type W = number;
export type L = number;
export type Otl = number;
export type Sol = number;
export type Gf = number;
export type Ga = number;
export type Games = number;
export type FullBoxScores = number;
export type ScoreOnly = number;
export type Partial = number;
export type PpEntered = number;
export type SpecialTeamsGames = number;
export type Notes = string[];
export type GameId = number;
export type Date = string | null;
export type Home = boolean;
export type Opponent = string;
export type Gf1 = number;
export type Ga1 = number;
export type Result = string;
export type Last5 = GameResult[];
export type OpponentsWinPct = number | null;
export type OpponentsGames = number;
export type Games1 = number;
export type ShotGames = number;
export type Period = string;
export type Gf2 = number;
export type Ga2 = number;
export type Sf = number;
export type Sa = number;
export type Rows = PeriodRow[];
export type FirstGoalGames = number;
export type Games2 = number;
export type SfPerGame = number | null;
export type SaPerGame = number | null;
export type ShotShare = number | null;
export type Games3 = number;
export type PpOpportunities = number;
export type PpGoals = number;
export type PpPct = number | null;
export type PkOpportunities = number;
export type PpGoalsAgainst = number;
export type PkPct = number | null;
export type PpMinutesPerGame = number | null;
export type PkMinutesPerGame = number | null;
export type PpGoalsPer60 = number | null;
export type ShGoals = number;
export type ShGoalsAgainst = number;
export type EnteredDisagrees = number;
export type Games4 = number;
export type PimPerGame = number | null;
export type InfractionsPerGame = number | null;
export type MinorsPerGame = number | null;
export type Infraction = string;
export type Count = number;
export type PerGame = number;
export type LeaguePerGame = number;
export type Infractions = InfractionCount[];
export type OtherInfractions = number;
export type PlayerId = number;
export type Name = string;
export type Penalties = number;
export type Pim = number;
export type TopTakers = PenaltyTaker[];
export type Games5 = number;
export type Goals = number;
export type PlayerId1 = number;
export type Name1 = string;
export type Gp1 = number;
export type G = number;
export type A = number;
export type P = number;
export type PpGoals1 = number;
export type Md1Rank = number | null;
export type Md1Tied = boolean;
export type Md1Points = number | null;
export type Top = Scorer[];
export type Top3GoalShare = number | null;
export type PpGoalShare = number | null;
export type Md1Skaters = number;
export type Games6 = number;
export type PlayerId2 = number;
export type Name2 = string;
export type Gp2 = number;
export type Starts = number;
export type Minutes = number;
export type ShotsAgainst = number;
export type GoalsAgainst = number;
export type SvPct = number | null;
export type Gaa = number | null;
export type Replaced = number;
export type Goalies = Goalie[];
export type GameId1 = number;
export type Date1 = string | null;
export type Weekday = string;
export type Home1 = boolean;
export type Goalie1 = string;
export type RecentStarts = GoalieStart[];
export type Metric = string;
export type Value = number | null;
export type Rank = number | null;
export type Of = number;
export type HigherIsBetter = boolean;
export type Ranks = LeagueRank[];
export type Season1 = string;
export type GameId2 = number;
export type Date2 = string | null;
export type Home2 = boolean;
export type Gf3 = number;
export type Ga3 = number;
export type Result1 = string;
export type Venue = string;
export type HeadToHead = HeadToHead1[];
export type GameId3 = number;
export type Date3 = string | null;
export type Home3 = boolean;
export type OpponentId = number;
export type Opponent1 = string;
export type NextGames = UpcomingGame[];
export type TeamId1 = number;
export type Team1 = string;
export type Rank1 = number | null;
export type Source = ("acha" | "rebuilt") | null;
export type Of1 = number;
export type Games7 = number;
export type OfficialAsOf = string | null;
export type Line = number;

export interface ScoutingReport {
  team_id: TeamId;
  team: Team;
  division: Division;
  built_at: BuiltAt;
  current: SeasonBlock;
  prior: SeasonBlock | null;
  head_to_head: HeadToHead;
  next_games: NextGames;
  national: National;
}
export interface SeasonBlock {
  season_id: SeasonId;
  season: Season;
  record: Record;
  quality: Quality;
  form: Form;
  by_period: ByPeriod;
  shots: Shots;
  special_teams: SpecialTeams;
  discipline: Discipline;
  scoring: Scoring;
  goaltending: Goaltending;
  ranks: Ranks;
}
export interface Record {
  gp: Gp;
  w: W;
  l: L;
  otl: Otl;
  sol: Sol;
  gf: Gf;
  ga: Ga;
}
export interface Quality {
  games: Games;
  full_box_scores: FullBoxScores;
  score_only: ScoreOnly;
  partial: Partial;
  pp_entered: PpEntered;
  special_teams_games: SpecialTeamsGames;
  notes: Notes;
}
export interface Form {
  last5: Last5;
  home: Record;
  road: Record;
  one_goal: Record;
  overtime: Record;
  opponents_win_pct: OpponentsWinPct;
  opponents_games: OpponentsGames;
}
export interface GameResult {
  game_id: GameId;
  date: Date;
  home: Home;
  opponent: Opponent;
  gf: Gf1;
  ga: Ga1;
  result: Result;
}
export interface ByPeriod {
  games: Games1;
  shot_games: ShotGames;
  rows: Rows;
  scored_first: Record;
  allowed_first: Record;
  first_goal_games: FirstGoalGames;
  leading_after_two: Record;
  tied_after_two: Record;
  trailing_after_two: Record;
}
export interface PeriodRow {
  period: Period;
  gf: Gf2;
  ga: Ga2;
  sf: Sf;
  sa: Sa;
}
export interface Shots {
  games: Games2;
  sf_per_game: SfPerGame;
  sa_per_game: SaPerGame;
  shot_share: ShotShare;
}
export interface SpecialTeams {
  games: Games3;
  pp_opportunities: PpOpportunities;
  pp_goals: PpGoals;
  pp_pct: PpPct;
  pk_opportunities: PkOpportunities;
  pp_goals_against: PpGoalsAgainst;
  pk_pct: PkPct;
  pp_minutes_per_game: PpMinutesPerGame;
  pk_minutes_per_game: PkMinutesPerGame;
  pp_goals_per_60: PpGoalsPer60;
  sh_goals: ShGoals;
  sh_goals_against: ShGoalsAgainst;
  entered_disagrees: EnteredDisagrees;
}
export interface Discipline {
  games: Games4;
  pim_per_game: PimPerGame;
  infractions_per_game: InfractionsPerGame;
  minors_per_game: MinorsPerGame;
  infractions: Infractions;
  other_infractions: OtherInfractions;
  top_takers: TopTakers;
}
export interface InfractionCount {
  infraction: Infraction;
  count: Count;
  per_game: PerGame;
  league_per_game: LeaguePerGame;
}
export interface PenaltyTaker {
  player_id: PlayerId;
  name: Name;
  penalties: Penalties;
  pim: Pim;
}
export interface Scoring {
  games: Games5;
  goals: Goals;
  top: Top;
  top3_goal_share: Top3GoalShare;
  pp_goal_share: PpGoalShare;
  md1_skaters: Md1Skaters;
}
export interface Scorer {
  player_id: PlayerId1;
  name: Name1;
  gp: Gp1;
  g: G;
  a: A;
  p: P;
  pp_goals: PpGoals1;
  md1_rank: Md1Rank;
  md1_tied: Md1Tied;
  md1_points: Md1Points;
}
export interface Goaltending {
  games: Games6;
  goalies: Goalies;
  recent_starts: RecentStarts;
}
export interface Goalie {
  player_id: PlayerId2;
  name: Name2;
  gp: Gp2;
  starts: Starts;
  minutes: Minutes;
  shots_against: ShotsAgainst;
  goals_against: GoalsAgainst;
  sv_pct: SvPct;
  gaa: Gaa;
  replaced: Replaced;
}
export interface GoalieStart {
  game_id: GameId1;
  date: Date1;
  weekday: Weekday;
  home: Home1;
  goalie: Goalie1;
}
export interface LeagueRank {
  metric: Metric;
  value: Value;
  rank: Rank;
  of: Of;
  higher_is_better: HigherIsBetter;
}
export interface HeadToHead1 {
  season: Season1;
  game_id: GameId2;
  date: Date2;
  home: Home2;
  gf: Gf3;
  ga: Ga3;
  result: Result1;
  venue: Venue;
}
export interface UpcomingGame {
  game_id: GameId3;
  date: Date3;
  home: Home3;
  opponent_id: OpponentId;
  opponent: Opponent1;
}
/**
 * Where the team sits nationally, beside UC: the ranks Where we stand shows.
 */
export interface National {
  team: NationalRank;
  uc: NationalRank | null;
  official_as_of: OfficialAsOf;
  line: Line;
}
export interface NationalRank {
  team_id: TeamId1;
  team: Team1;
  rank: Rank1;
  source: Source;
  of: Of1;
  games: Games7;
}
