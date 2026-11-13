import { useEffect } from "react";
import { dec, ordinal, shortDate } from "../format";
import type { RankCheck, RankingRow, RankingsReport } from "../types/rankings";
import { Disclaimer, Section, SheetHeader, Tile } from "./parts";

/* Where we stand: the ACHA's M1 computer ranking, which alone picks the 24-team national
   field, rebuilt from game rows. Once the ACHA publishes its own weekly ranking, its rank
   leads and ours sits beside it. No projection and no chart: today, the line, what's left,
   and how teams ranked like this have finished before. */

/** The rank to lead with: the ACHA's when published, else our rebuild. */
export function headlineRank(row: Pick<RankingRow, "rank" | "official_rank">):
  { shown: number | null; rebuilt: number | null; official: boolean } {
  return row.official_rank
    ? { shown: row.official_rank, rebuilt: row.rank ?? null, official: true }
    : { shown: row.rank ?? null, rebuilt: row.rank ?? null, official: false };
}

const record = (r: RankingRow) => `${r.w}-${r.l}-${r.t}`;

const andList = (items: string[]) =>
  items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;

function placesText(places: number): string {
  if (places === 0) return "on the line";
  return places > 0 ? `${places} above` : `${-places} below`;
}

function Banner({ report }: { report: RankingsReport }) {
  return (
    <div className="keep my-2 rounded px-2 py-1 text-[0.72rem] leading-snug"
         style={{ background: "var(--notice)", color: "var(--notice-ink)" }}>
      <strong>Sample: {report.games} MD1-vs-MD1 games through {shortDate(report.games_through)}, a median of{" "}
        {dec(report.median_games, 0)} per team.</strong>{" "}
      "Rebuilt" means our own recalculation of the ACHA's computer ranking from the league's game results, by its
      published method: only games between two MD1 teams count, overtime and shootout results count as ties, and
      forfeits are left out. The ACHA's own ranking, which picks the field, leads wherever it has been published.
    </div>
  );
}

function UcBlock({ report, teamHref }: { report: RankingsReport; teamHref: (id: number) => string }) {
  const uc = report.uc!;
  const head = headlineRank(uc);
  const row = report.rows.find((r) => r.team_id === uc.team_id)!;
  const rebuiltLine = `with games through ${shortDate(report.games_through)}`;
  const rate = uc.base_rate;
  return (
    <>
      <div className="grid grid-cols-3 gap-1.5">
        <Tile label="Cincinnati's rank" value={head.shown ? ordinal(head.shown) : "–"}
              sub={head.official
                ? `ACHA ranking as of ${shortDate(report.official_as_of)} · rebuilt ${head.rebuilt ? ordinal(head.rebuilt) : "–"} ${rebuiltLine}`
                : `Rebuilt ${rebuiltLine} · Ranking A ${row.rank_a ? ordinal(row.rank_a) : "–"}, B ${row.rank_b ? ordinal(row.rank_b) : "–"}`} />
        <Tile label="Places from the line" value={uc.places_from_line === null || uc.places_from_line === undefined
                ? "–" : placesText(uc.places_from_line)}
              sub={`The top ${report.line} make nationals; a conference champion ranked below that bumps the lowest.`} />
        <Tile label="MD1 record, the ACHA's way" value={record(row)}
              sub={row.otw || row.otl ? `W-L-T; the ties include ${row.otw} OT/SO wins and ${row.otl} losses`
                : "W-L-T, counting only MD1-vs-MD1 games"} />
      </div>
      {rate && (
        <p className="mt-1 text-[0.72rem]">
          Of {rate.teams} teams ranked {ordinal(rate.low)}–{ordinal(rate.high)} on {rate.on} in {andList(rate.seasons)},{" "}
          {rate.made_line} finished in the top {report.line}.
        </p>
      )}
      {uc.remaining.length > 0 && (
        <Section title="What's left" n={`${uc.remaining.length} MD1 games left`} className="mt-2" split>
          <table className="data">
            <thead><tr><th>Date</th><th></th><th>Opponent</th><th className="r">Their rank</th></tr></thead>
            <tbody>
              {uc.remaining.map((g) => {
                const opp = headlineRank({ rank: g.opponent_rank, official_rank: g.opponent_official_rank });
                return (
                  <tr key={g.game_id}>
                    <td>{shortDate(g.date)}</td><td>{g.home ? "vs" : "@"}</td>
                    <td><a href={teamHref(g.opponent_id)}>{g.opponent}</a></td>
                    <td className="r">{opp.shown ? ordinal(opp.shown) : "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Section>
      )}
    </>
  );
}

function RankTable({ report, teamHref }: {
  report: RankingsReport; teamHref: (id: number) => string;
}) {
  const ucId = report.uc?.team_id ?? 265;
  const official = report.official_as_of !== null && report.official_as_of !== undefined;
  // Led by the ACHA's ranking, a team it hasn't ranked goes last, unnumbered, rather than
  // borrowing a rebuilt number that could repeat one of the ACHA's.
  const shownRank = (r: RankingRow) => (official ? r.official_rank : r.rank) ?? null;
  const rows = [...report.rows].sort((a, b) =>
    (shownRank(a) ?? 999) - (shownRank(b) ?? 999) || a.team.localeCompare(b.team));
  const aboveLine = rows.filter((r) => { const s = shownRank(r); return s !== null && s <= report.line; }).length;
  // The ≠ marks come from the check against the latest official ranking, the same comparison
  // the section below lists: our rebuild as of that ranking's date, not with later games.
  const latest = report.checks[report.checks.length - 1];
  const differences = new Map((latest?.differences ?? []).map((d) => [d.team_id, d]));
  return (
    <Section title={official ? `The ranking: the ACHA's, as of ${shortDate(report.official_as_of)}` : "The ranking, rebuilt"}
             n={`${report.rows.length} MD1 teams`} split>
      <table className="data" aria-label="MD1 ranking">
        <thead><tr><th className="r">#</th><th>Team</th><th>Conf.</th><th className="r">W-L-T</th>
          <th className="r">A</th><th className="r">B</th><th className="r">Avg</th><th className="r">Sched.</th></tr></thead>
        <tbody>
          {rows.flatMap((r, i) => {
            const head = headlineRank(r);
            const diff = official ? differences.get(r.team_id) : undefined;
            const uc = r.team_id === ucId;
            const out = [
              <tr key={r.team_id} data-highlight={uc ? "uc" : undefined}
                  style={uc ? { background: "var(--notice)", fontWeight: 600 } : undefined}>
                <td className="r">{shownRank(r) ?? "–"}</td>
                <td><a href={teamHref(r.team_id)}>{r.team}</a>
                  {diff && <span title={`rebuilt ${diff.rebuilt ? ordinal(diff.rebuilt) : "unranked"} as of ${shortDate(latest!.as_of)}`}
                                 style={{ color: "var(--accent)" }}> ≠</span>}</td>
                <td>{r.league}</td><td className="r">{record(r)}</td>
                {/* Led by the ACHA's rank, the row shows the ACHA's columns; ≠ points to ours. */}
                <td className="r">{(head.official ? r.official_rank_a : r.rank_a) ?? "–"}</td>
                <td className="r">{(head.official ? r.official_rank_b : r.rank_b) ?? "–"}</td>
                <td className="r">{dec(head.official ? r.official_avg : r.avg, 1)}</td>
                <td className="r">{dec(r.schedule, 2)}</td>
              </tr>,
            ];
            if (i === aboveLine - 1) {
              out.push(
                <tr key="line" className="keep">
                  <td colSpan={8} className="text-[0.68rem] font-semibold"
                      style={{ borderTop: "2px solid var(--accent)", color: "var(--accent)" }}>
                    Nationals line ({report.line}). Conference champions ranked below it bump the lowest teams.
                  </td>
                </tr>,
              );
            }
            return out;
          })}
        </tbody>
      </table>
      <p className="mt-1 text-[0.68rem]" style={{ color: "var(--muted)" }}>
        A caps each game's goal difference at 7; B counts only wins, losses and ties. The order is their average rank, ties
        to B{official ? " (the ACHA's own A, B and average)" : ""}. W-L-T and Sched. are from our rebuild; Sched. is the
        average Ranking A rating of the opponents played (0 = an average MD1 team).
        {official && ` ≠: our rebuild, as of the ACHA's ${shortDate(report.official_as_of)} ranking, has the team 3 or more places away; hover for its rank.`}
      </p>
    </Section>
  );
}

function checkLine(c: RankCheck): string {
  const short = c.our_games_through && c.our_games_through < c.as_of
    ? ` (our games through ${shortDate(c.our_games_through)})` : "";
  return `${shortDate(c.as_of)}: ${c.agree_exact} of ${c.teams} at the same rank, ${c.within_2} within 2 places; ` +
    `${c.top24_match ? "same top 24" : "different top 24"}${short}.`;
}

function Checks({ report }: { report: RankingsReport }) {
  const latest = report.checks[report.checks.length - 1];
  const early = report.games_through ? Number(report.games_through.slice(5, 7)) : 0;
  return (
    <Section title="Checked against the ACHA" n={`${report.checks.length} official rankings`}>
      {report.checks.length === 0 ? (
        <p className="text-[0.72rem]">
          {early >= 8 && early <= 11
            ? "The ACHA hasn't published a computer ranking for this season yet; the first comes out after November 1. Until then this page shows only the rebuild."
            : "No official ACHA ranking is stored for this season."}
        </p>
      ) : (
        <>
          <ul className="text-[0.72rem] leading-snug">
            {report.checks.map((c) => <li key={c.as_of}>{checkLine(c)}</li>)}
          </ul>
          {latest.differences.length > 0 && (
            <p className="mt-1 text-[0.68rem]" style={{ color: "var(--ink-2)" }}>
              Latest differences of 3+ places:{" "}
              {latest.differences.map((d) =>
                `${d.team.replace(/^MD[12] /, "")} (ACHA ${ordinal(d.official)}, rebuilt ${d.rebuilt ? ordinal(d.rebuilt) : "unranked"}${d.cause ? `: ${d.cause}` : ""})`,
              ).join("; ")}. A difference usually means a game the two sides count differently.
            </p>
          )}
        </>
      )}
    </Section>
  );
}

export function RankingsPage({ report, teamHref = (id) => `?team=${id}`, freshness }: {
  report: RankingsReport; teamHref?: (id: number) => string; freshness?: string;
}) {
  useEffect(() => {
    document.documentElement.style.setProperty("--print-team", JSON.stringify("Where we stand"));
  }, []);
  return (
    <article className="sheet flow mx-auto max-w-[8.5in] p-6 shadow-sm" style={{ background: "var(--surface)" }}>
      <SheetHeader kicker={`Where we stand · ${report.season} · ACHA Men's Division 1`} title="The road to nationals"
                   right={<div>Built {shortDate(report.built_at.slice(0, 10))} from {report.games} games</div>} />
      <Banner report={report} />
      {report.uc && <UcBlock report={report} teamHref={teamHref} />}
      <div className="mt-3"><RankTable report={report} teamHref={teamHref} /></div>
      <Checks report={report} />
      <Disclaimer team="Where we stand" freshness={freshness} />
    </article>
  );
}
