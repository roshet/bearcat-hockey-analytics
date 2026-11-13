/* Generated from the Pydantic RankingsReport model by npm run gen:types. Do not edit. */

export type SeasonId = number;
export type Season = string;
export type BuiltAt = string;
export type GamesThrough = string | null;
export type Games = number;
export type MedianGames = number;
export type Line = number;
export type TeamId = number;
export type Team = string;
export type Games1 = number;
export type Rank = number | null;
export type RankA = number | null;
export type RankB = number | null;
export type Avg = number | null;
export type RatingA = number;
export type RatingB = number;
export type Schedule = number | null;
export type W = number;
export type L = number;
export type T = number;
export type Otw = number;
export type Otl = number;
export type League = string;
export type OfficialRank = number | null;
export type OfficialRankA = number | null;
export type OfficialRankB = number | null;
export type OfficialAvg = number | null;
export type Rows = RankingRow[];
export type OfficialAsOf = string | null;
export type TeamId1 = number;
export type Team1 = string;
export type Rank1 = number | null;
export type OfficialRank1 = number | null;
export type PlacesFromLine = number | null;
export type GameId = number;
export type Date = string | null;
export type Home = boolean;
export type OpponentId = number;
export type Opponent = string;
export type OpponentRank = number | null;
export type OpponentOfficialRank = number | null;
export type Remaining = RemainingGame[];
export type Seasons = string[];
export type On = string;
export type Low = number;
export type High = number;
export type Teams = number;
export type MadeLine = number;
export type AsOf = string;
export type OurGamesThrough = string | null;
export type Teams1 = number;
export type AgreeExact = number;
export type Within2 = number;
export type Top24Match = boolean;
export type TeamId2 = number;
export type Team2 = string;
export type Official = number;
export type Rebuilt = number | null;
export type Cause = string | null;
export type Differences = RankDifference[];
export type Checks = RankCheck[];

export interface RankingsReport {
  season_id: SeasonId;
  season: Season;
  built_at: BuiltAt;
  games_through: GamesThrough;
  games: Games;
  median_games: MedianGames;
  line: Line;
  rows: Rows;
  official_as_of: OfficialAsOf;
  uc: TeamStanding | null;
  checks: Checks;
}
export interface RankingRow {
  team_id: TeamId;
  team: Team;
  games: Games1;
  rank: Rank;
  rank_a: RankA;
  rank_b: RankB;
  avg: Avg;
  rating_a: RatingA;
  rating_b: RatingB;
  schedule: Schedule;
  w: W;
  l: L;
  t: T;
  otw: Otw;
  otl: Otl;
  league: League;
  official_rank: OfficialRank;
  official_rank_a: OfficialRankA;
  official_rank_b: OfficialRankB;
  official_avg: OfficialAvg;
}
export interface TeamStanding {
  team_id: TeamId1;
  team: Team1;
  rank: Rank1;
  official_rank: OfficialRank1;
  places_from_line: PlacesFromLine;
  remaining: Remaining;
  base_rate: BaseRate | null;
}
export interface RemainingGame {
  game_id: GameId;
  date: Date;
  home: Home;
  opponent_id: OpponentId;
  opponent: Opponent;
  opponent_rank: OpponentRank;
  opponent_official_rank: OpponentOfficialRank;
}
/**
 * Of the teams ranked ``low``-``high`` on the same date in past seasons, how many
 * finished in the top 24. History, not a prediction (the user's choice for M6).
 */
export interface BaseRate {
  seasons: Seasons;
  on: On;
  low: Low;
  high: High;
  teams: Teams;
  made_line: MadeLine;
}
export interface RankCheck {
  as_of: AsOf;
  our_games_through: OurGamesThrough;
  teams: Teams1;
  agree_exact: AgreeExact;
  within_2: Within2;
  top24_match: Top24Match;
  differences: Differences;
}
export interface RankDifference {
  team_id: TeamId2;
  team: Team2;
  official: Official;
  rebuilt: Rebuilt;
  cause: Cause;
}
