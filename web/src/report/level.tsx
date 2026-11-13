import { dec, ordinal, pct, shortDate } from "../format";
import type { LeagueRank, National, NationalRank } from "../types/report";

/* How good the team is, at a glance: its national rank beside UC's (the same
   ranks Where we stand shows). */

/** "of 79 (rebuilt) · from 12 MD1 games": where a rank comes from and what it rests on. */
function rankDetail(r: NationalRank, asOf: string | null): string {
  const source = r.source === "acha" ? `ACHA, ${shortDate(asOf)}` : "rebuilt";
  return `of ${r.of} (${source}) · from ${r.games} MD1 games`;
}

/** "9th of 79 (rebuilt) · from 12 MD1 games": every rank says its source and its sample. */
export function rankText(r: NationalRank, asOf: string | null): string {
  if (r.rank === null || r.rank === undefined) return "not ranked yet";
  return `${ordinal(r.rank)} ${rankDetail(r, asOf)}`;
}

/** The ordinal in semibold, then its source and sample: both ranks on the line read alike. */
function RankLine({ r, asOf }: { r: NationalRank; asOf: string | null }) {
  if (!r.rank) return <>{rankText(r, asOf)}</>;
  return <><strong data-rank className="font-semibold" style={{ color: "var(--ink)" }}>{ordinal(r.rank)}</strong>{" "}{rankDetail(r, asOf)}</>;
}

export function NationalBlock({ national }: { national: National }) {
  const asOf = national.official_as_of ?? null;
  return (
    <div className="keep mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-0.5 text-xs" style={{ color: "var(--ink-2)" }}>
      <div>
        <span className="text-[0.65rem] uppercase tracking-wide" style={{ color: "var(--muted)" }}>National rank </span>
        <RankLine r={national.team} asOf={asOf} />
      </div>
      {national.uc && (
        <div>
          {national.uc.team}: <RankLine r={national.uc} asOf={asOf} />
        </div>
      )}
      <div className="text-[0.65rem]" style={{ color: "var(--muted)" }}>
        The top {national.line} make nationals.
      </div>
    </div>
  );
}

/* Strengths and weaknesses: each ranked area as a bar of how high the team sits among
   MD1 teams, best first. Only the top and bottom quarters get a word: at about ten
   games a middle rank is mostly noise. The words carry the meaning (never colour), and
   every rank is printed, so the chart reads the same on paper as on screen. */

const METRICS: Record<string, { label: string; value: (v: number) => string }> = {
  "goals for per game": { label: "Goals for / game", value: (v) => dec(v, 2) },
  "goals against per game": { label: "Goals against / game", value: (v) => dec(v, 2) },
  "shot share": { label: "Shot share", value: (v) => pct(v) },
  "power play %": { label: "Power play", value: (v) => pct(v) },
  "penalty kill %": { label: "Penalty kill", value: (v) => pct(v) },
  "penalty minutes per game": { label: "Fewest penalty minutes", value: (v) => dec(v, 1) },
};

export function strengthLabel(rank: number, of: number): "Strength" | "Weakness" | null {
  if (rank <= of / 4) return "Strength";
  if (rank > (3 * of) / 4) return "Weakness";
  return null;
}

/** 1 for the best team, 0 for the worst. */
const standing = (rank: number, of: number): number => (of > 1 ? 1 - (rank - 1) / (of - 1) : 1);

export function StrengthsChart({ ranks, gp }: { ranks: LeagueRank[]; gp: number }) {
  const ranked = ranks
    .filter((r): r is LeagueRank & { rank: number } => typeof r.rank === "number" && r.of > 0)
    .sort((a, b) => standing(b.rank, b.of) - standing(a.rank, a.of));
  if (ranked.length === 0) {
    return <p className="keep mt-2 text-xs" style={{ color: "var(--ink-2)" }}>Too early to rank: fewer than 5 games.</p>;
  }
  return (
    <div className="keep mt-2">
      <div className="text-[0.65rem] uppercase tracking-wide" style={{ color: "var(--muted)" }}>Against MD1</div>
      {/* Two columns of three from sm up (the printed page included), best first down the
          first column: a single column pushed page 1's footer onto a third page. */}
      <div className="grid grid-cols-1 gap-x-4 sm:grid-flow-col sm:grid-cols-2 sm:grid-rows-3">
      {ranked.map((r) => {
        const meta = METRICS[r.metric] ?? { label: r.metric, value: (v: number) => String(v) };
        const label = strengthLabel(r.rank, r.of);
        return (
          <div key={r.metric} data-metric={r.metric} className="flex items-center gap-2 text-xs leading-tight"
               title={r.value === null || r.value === undefined ? meta.label : `${meta.label}: ${meta.value(r.value)}`}>
            <span className="w-28 shrink-0 truncate sm:w-32">{meta.label}</span>
            <span className="h-2 flex-1 rounded-sm" style={{ background: "var(--hairline)" }}>
              <span className="block h-2 rounded-sm" style={{ width: `${standing(r.rank, r.of) * 100}%`, background: "var(--muted)" }} />
            </span>
            <span className="num w-16 shrink-0 text-right">{ordinal(r.rank)} of {r.of}</span>
            <span className="w-20 shrink-0 font-semibold">{label === "Strength" ? "▲ Strength" : label === "Weakness" ? "▼ Weakness" : ""}</span>
          </div>
        );
      })}
      </div>
      <p className="mt-0.5 text-[0.62rem]" style={{ color: "var(--muted)" }}>
        MD1 rank among the {Math.max(...ranked.map((r) => r.of))} teams with 5+ games; from {gp} games. Strength = top quarter, weakness = bottom
        quarter; the middle is mostly noise this early.
      </p>
    </div>
  );
}
