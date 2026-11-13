import { shortDate } from "./format";

/* The deployed site is static: `bca export static` writes every stored page under
   public/data, and the app reads those files. Nothing is computed per request. */

export interface DataIndex {
  current_season: number;
  seasons: number[];
  built_at: string;
  data_through: string | null;
}

export type DataKind = "teams" | "scouting" | "special-teams" | "rankings";

export function dataUrl(kind: DataKind, season: number, team?: number): string {
  if (kind === "teams" || kind === "rankings") return `/data/${season}/${kind}.json`;
  return `/data/${season}/${kind}/${team}.json`;
}

/** "Data through Oct 22 · updated Oct 24": shown on every sheet, so a stale night shows. */
export function freshnessText(index: DataIndex): string {
  const updated = `updated ${shortDate(index.built_at.slice(0, 10))}`;
  return index.data_through ? `Data through ${shortDate(index.data_through)} · ${updated}` : updated;
}
