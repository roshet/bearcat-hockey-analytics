import type { Record as TeamRecord } from "./types/report";

export const pct = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined ? "–" : `${(value * 100).toFixed(digits)}%`;

export const dec = (value: number | null | undefined, digits = 2): string =>
  value === null || value === undefined ? "–" : value.toFixed(digits);

/** A per-game rate to one decimal, without rounding a rare-but-real rate to "0.0". */
export const rate = (value: number | null | undefined): string =>
  value === null || value === undefined ? "–" : value > 0 && value < 0.05 ? "<0.1" : value.toFixed(1);

/** "10-2", or "10-2-1-0" once there are OT or shootout losses. */
export const recordText = (r: TeamRecord): string =>
  r.otl || r.sol ? `${r.w}-${r.l}-${r.otl}-${r.sol}` : `${r.w}-${r.l}`;

export const perGame = (total: number, games: number): number | null =>
  games ? total / games : null;

export const ordinal = (n: number): string => {
  const rem100 = n % 100;
  const suffix = rem100 >= 11 && rem100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] ?? "th");
  return `${n}${suffix}`;
};

export const shortDate = (iso: string | null | undefined): string => {
  if (!iso) return "TBD";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric" });
};
