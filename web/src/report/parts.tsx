import type { ReactNode } from "react";
import { ordinal } from "../format";

/* Building blocks shared by the scouting report and the special-teams page. */

/** A titled block. Printed whole unless `split`: a long table may then continue on the next
    page, with its header row repeated. */
export function Section({ title, n, children, className = "", split = false }: {
  title: string; n?: string; children: ReactNode; className?: string; split?: boolean;
}) {
  return (
    <section className={`mb-3 ${split ? "split" : ""} ${className}`}>
      <h2 className="mb-1 flex items-baseline justify-between gap-2 border-b pb-0.5 text-[0.7rem] font-bold uppercase tracking-wider"
          style={{ borderColor: "var(--accent)", color: "var(--ink)" }}>
        <span>{title}</span>
        {n && <span className="font-normal normal-case tracking-normal" style={{ color: "var(--muted)" }}>{n}</span>}
      </h2>
      {/* On a phone a wide table scrolls inside its section instead of widening the page.
          Print keeps overflow visible: a scroll box can't split across pages. */}
      <div className="overflow-x-auto print:overflow-visible">{children}</div>
    </section>
  );
}

export function rankOf(ranks: { metric: string; rank?: number | null; of: number }[], metric: string): string | null {
  const r = ranks.find((x) => x.metric === metric);
  return r?.rank ? `${ordinal(r.rank)} of ${r.of}` : null;
}

export function Tile({ label, value, sub, rank }: { label: string; value: string; sub?: string; rank?: string | null }) {
  return (
    <div className="keep rounded border px-2 py-1.5" style={{ borderColor: "var(--hairline)", background: "var(--surface)" }}>
      <div className="text-[0.65rem] uppercase tracking-wide" style={{ color: "var(--muted)" }}>{label}</div>
      <div className="text-xl font-semibold leading-tight">{value}</div>
      <div className="text-[0.65rem] leading-snug" style={{ color: "var(--ink-2)" }}>
        {rank && <div>MD1 rank {rank}</div>}
        {sub && <div>{sub}</div>}
      </div>
    </div>
  );
}

export const DISCLAIMER =
  "Independent student project — not affiliated with or endorsed by the University of Cincinnati or the ACHA. " +
  "Computed from the league's public game records; derived figures are estimates and say how many games they rest on. " +
  "Data courtesy of the ACHA and FloHockey.";

/** Once per printed page: a plain block, since fixed elements don't repeat reliably in print.
    Without `page` it's the on-screen copy of a flowing sheet, whose printed footer is the
    `@page flow` margin boxes instead (see index.css). */
export function Disclaimer({ page, team, freshness }: { page?: string; team: string; freshness?: string }) {
  return (
    <footer className={`keep mt-2 flex flex-col gap-1 border-t pt-1 text-[0.62rem] sm:flex-row sm:justify-between sm:gap-4 ${page ? "" : "print:hidden"}`}
            style={{ borderColor: "var(--hairline)", color: "var(--muted)" }}>
      <span>{DISCLAIMER}</span>
      <span className="sm:whitespace-nowrap">{freshness && `${freshness} · `}{team}{page && ` · page ${page}`}</span>
    </footer>
  );
}

/** The black header band at the top of each sheet. */
export function SheetHeader({ kicker, title, right }: { kicker: string; title: string; right: ReactNode }) {
  return (
    <header className="keep -mx-6 -mt-6 mb-2 flex flex-col gap-1 px-6 py-2 sm:flex-row sm:items-end sm:justify-between sm:gap-4 print:mx-0 print:mt-0 print:px-3"
            style={{ background: "var(--band)", color: "var(--band-ink)", borderBottom: "3px solid var(--accent)" }}>
      <div>
        <div className="text-[0.65rem] uppercase tracking-widest opacity-80">{kicker}</div>
        <h1 className="text-2xl font-bold leading-tight">{title}</h1>
      </div>
      <div className="text-[0.7rem] leading-snug opacity-90 sm:text-right">{right}</div>
    </header>
  );
}
