import type { ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, Tooltip, XAxis, YAxis } from "recharts";
import type { PeriodRow } from "../types/report";

/* Mark specs (dataviz skill): bars <= 24px with a 4px rounded data-end and a
   square baseline, a 2px surface gap between adjacent bars, solid hairline
   grid, values on the caps in text ink (never the series colour). Fills come
   from CSS tokens via class names, so light, dark and print all swap in one
   place. Fixed sizes: these must print identically. */

const axisTick = { fill: "var(--muted)", fontSize: 10 };
const capLabel = { fill: "var(--ink-2)", fontSize: 10 };

function ChartTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: { name: string; value: number }[];
  label?: string;
}): ReactNode {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded border px-2 py-1 text-xs"
         style={{ background: "var(--surface)", borderColor: "var(--hairline)", color: "var(--ink)" }}>
      <div className="font-semibold">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="num">{p.name}: {p.value}</div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; token: string }[] }) {
  return (
    <div className="flex gap-3 text-xs" style={{ color: "var(--ink-2)" }}>
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: `var(${item.token})` }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

/** Goals for and against in each period: two series, so a legend plus cap labels. */
export function PeriodGoalsChart({ rows }: { rows: PeriodRow[] }) {
  const data = rows.map((r) => ({ period: r.period === "OT" ? "OT" : `P${r.period}`, For: r.gf, Against: r.ga }));
  return (
    <div className="keep">
      <Legend items={[{ label: "Goals for", token: "--series-for" }, { label: "Goals against", token: "--series-against" }]} />
      <BarChart width={180} height={130} data={data} margin={{ top: 16, right: 4, bottom: 0, left: -24 }}
                barGap={2} barCategoryGap="28%">
        <CartesianGrid vertical={false} stroke="var(--hairline)" />
        <XAxis dataKey="period" tick={axisTick} axisLine={{ stroke: "var(--axis)" }} tickLine={false} />
        <YAxis allowDecimals={false} tick={axisTick} axisLine={false} tickLine={false} />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--hairline)", opacity: 0.4 }} />
        <Bar dataKey="For" className="bar-for" maxBarSize={22} radius={[4, 4, 0, 0]} isAnimationActive={false}>
          <LabelList dataKey="For" position="top" style={capLabel} />
        </Bar>
        <Bar dataKey="Against" className="bar-against" maxBarSize={22} radius={[4, 4, 0, 0]} isAnimationActive={false}>
          <LabelList dataKey="Against" position="top" style={capLabel} />
        </Bar>
      </BarChart>
    </div>
  );
}

