import { dec, ordinal, pct, perGame, rate, recordText, shortDate } from "../format";
import type { Scorer, ScoutingReport, SeasonBlock, Record as TeamRecord } from "../types/report";
import { PeriodGoalsChart } from "./charts";
import { NationalBlock, StrengthsChart } from "./level";
import { Disclaimer, Section, SheetHeader, Tile } from "./parts";

/* An opponent profile, print-first: two US Letter pages. UC appears only in
   the head-to-head section. Every block states the games it rests on. */

const rec = (r: TeamRecord) => `${recordText(r)} (${r.gf}-${r.ga})`;

function Headline({ cur, prior }: { cur: SeasonBlock; prior?: SeasonBlock | null }) {
  const gp = cur.record.gp;
  const last = (fn: (b: SeasonBlock) => string) => (prior ? `${prior.season}: ${fn(prior)}` : undefined);
  const st = cur.special_teams;
  return (
    <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-6">
      <Tile label="Record" value={recordText(cur.record)} sub={last((b) => recordText(b.record))} />
      <Tile label="Goals for / game" value={dec(perGame(cur.record.gf, gp))} sub={last((b) => dec(perGame(b.record.gf, b.record.gp)))} />
      <Tile label="Goals against / game" value={dec(perGame(cur.record.ga, gp))} sub={last((b) => dec(perGame(b.record.ga, b.record.gp)))} />
      <Tile label={`Power play (${st.pp_goals}/${st.pp_opportunities})`} value={pct(st.pp_pct)} sub={last((b) => pct(b.special_teams.pp_pct))} />
      <Tile label={`Penalty kill (${st.pk_opportunities - st.pp_goals_against}/${st.pk_opportunities})`}
            value={pct(st.pk_pct)} sub={last((b) => pct(b.special_teams.pk_pct))} />
      <Tile label="Shot share" value={pct(cur.shots.shot_share)}
            sub={last((b) => pct(b.shots.shot_share))} />
    </div>
  );
}

function Banner({ cur }: { cur: SeasonBlock }) {
  const q = cur.quality;
  return (
    <div className="keep my-2 rounded px-2 py-1 text-[0.72rem] leading-snug"
         style={{ background: "var(--notice)", color: "var(--notice-ink)" }}>
      <strong>Sample: {q.games} games.</strong>{" "}
      {q.full_box_scores} full box scores
      {q.score_only ? `, ${q.score_only} score-only` : ""}{q.partial ? `, ${q.partial} partly entered` : ""}.
      {" "}Special teams are derived from the penalty log ({q.special_teams_games} games); the league's own
      PP field was entered in {q.pp_entered}. At this sample size, treat rates as indications, not findings.
      {q.notes.length > 0 && <> {q.notes.join(" ")}</>}
    </div>
  );
}

function ResultsSection({ cur }: { cur: SeasonBlock }) {
  const f = cur.form;
  const sub = "mb-0.5 text-[0.68rem]";
  return (
    <Section title="Results" n={`${cur.record.gp} GP`}>
      <div className={sub} style={{ color: "var(--muted)" }}>Last 5 games</div>
      <table className="data">
        <thead><tr><th>Date</th><th></th><th>Opponent</th><th className="r">Score</th><th>Result</th></tr></thead>
        <tbody>
          {f.last5.map((g) => (
            <tr key={g.game_id}>
              <td>{shortDate(g.date)}</td><td>{g.home ? "vs" : "@"}</td><td>{g.opponent}</td>
              <td className="r">{g.gf}-{g.ga}</td><td>{g.result}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className={`${sub} mt-1.5`} style={{ color: "var(--muted)" }}>Splits</div>
      <table className="data">
        <tbody>
          <tr><td>Home</td><td className="r">{rec(f.home)}</td><td>Road</td><td className="r">{rec(f.road)}</td></tr>
          <tr><td>One-goal games</td><td className="r">{recordText(f.one_goal)}</td>
              <td>Past regulation</td><td className="r">{f.overtime.gp ? recordText(f.overtime) : "none"}</td></tr>
          <tr><td colSpan={3}>Strength of schedule</td><td className="r">{pct(f.opponents_win_pct)}</td></tr>
        </tbody>
      </table>
      <p className="mt-0.5 text-[0.65rem]" style={{ color: "var(--muted)" }}>
        Strength of schedule: their opponents' win % in their other games ({f.opponents_games} GP).
      </p>
    </Section>
  );
}

function PeriodSection({ cur }: { cur: SeasonBlock }) {
  const p = cur.by_period;
  const share = (sf: number, sa: number) => (sf + sa ? pct(sf / (sf + sa), 0) : "–");
  return (
    <Section title="By period" n={`goals ${p.games} GP · shots ${p.shot_games} GP`}>
      <div className="flex flex-wrap items-start gap-2">
        <PeriodGoalsChart rows={p.rows} />
        <table className="data min-w-0 flex-1">
          <thead><tr><th>Per.</th><th className="r">GF</th><th className="r">GA</th><th className="r">SF</th><th className="r">SA</th><th className="r">Shot %</th></tr></thead>
          <tbody>
            {p.rows.map((r) => (
              <tr key={r.period}>
                <td>{r.period}</td><td className="r">{r.gf}</td><td className="r">{r.ga}</td>
                <td className="r">{r.sf}</td><td className="r">{r.sa}</td><td className="r">{share(r.sf, r.sa)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <table className="data mt-1.5">
        <tbody>
          <tr><td>Scored first</td><td className="r">{recordText(p.scored_first)}</td>
              <td>Allowed first</td><td className="r">{recordText(p.allowed_first)}</td></tr>
          <tr><td>Leading after 2</td><td className="r">{recordText(p.leading_after_two)}</td>
              <td>Trailing after 2</td><td className="r">{recordText(p.trailing_after_two)}</td></tr>
          <tr><td>Tied after 2</td><td className="r">{recordText(p.tied_after_two)}</td>
              <td style={{ color: "var(--muted)" }} colSpan={2}>
                {p.first_goal_games < p.games ? `first goal known in ${p.first_goal_games} of ${p.games} GP` : ""}
              </td></tr>
        </tbody>
      </table>
    </Section>
  );
}

function SpecialTeamsSection({ cur, prior }: { cur: SeasonBlock; prior?: SeasonBlock | null }) {
  const rows = [cur, ...(prior ? [prior] : [])];
  return (
    <Section title="Special teams (derived from the penalty log)" n={`${cur.special_teams.games} GP`}>
      <table className="data">
        <thead><tr><th>Season</th><th className="r">PP</th><th className="r">PP %</th><th className="r">PP min/GP</th>
          <th className="r">PK</th><th className="r">PK %</th><th className="r">PK min/GP</th>
          <th className="r">SHG for</th><th className="r">SHG agst</th></tr></thead>
        <tbody>
          {rows.map((b) => {
            const s = b.special_teams;
            return (
              <tr key={b.season_id}>
                <td>{b.season}</td>
                <td className="r">{s.pp_goals}/{s.pp_opportunities}</td><td className="r">{pct(s.pp_pct)}</td>
                <td className="r">{dec(s.pp_minutes_per_game, 1)}</td>
                <td className="r">{s.pk_opportunities - s.pp_goals_against}/{s.pk_opportunities}</td>
                <td className="r">{pct(s.pk_pct)}</td><td className="r">{dec(s.pk_minutes_per_game, 1)}</td>
                <td className="r">{s.sh_goals}</td><td className="r">{s.sh_goals_against}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-1 text-[0.68rem]" style={{ color: "var(--muted)" }}>
        PP time and opportunities are rebuilt second by second from the penalties; the league never publishes PP
        time. {cur.special_teams.entered_disagrees > 0 &&
          `In ${cur.special_teams.entered_disagrees} games the scorekeeper's PP-opportunity count differs from the derived one.`}
      </p>
    </Section>
  );
}

function DisciplineSection({ cur }: { cur: SeasonBlock }) {
  const d = cur.discipline;
  return (
    <Section title="Discipline" n={`${d.games} GP`}>
      <div className="mb-1 flex gap-4 text-xs">
        <span><strong className="num">{dec(d.pim_per_game, 1)}</strong> PIM/GP
</span>
        <span><strong className="num">{dec(d.minors_per_game, 1)}</strong> minors/GP</span>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <table className="data">
          <thead><tr><th>What they take</th><th className="r">Count</th><th className="r">Per GP</th><th className="r">League per GP</th></tr></thead>
          <tbody>
            {d.infractions.map((i) => (
              <tr key={i.infraction}><td>{i.infraction}</td><td className="r">{i.count}</td>
                <td className="r">{rate(i.per_game)}</td><td className="r" style={{ color: "var(--ink-2)" }}>{rate(i.league_per_game)}</td></tr>
            ))}
            {d.other_infractions > 0 && (
              <tr><td style={{ color: "var(--muted)" }}>Everything else</td><td className="r">{d.other_infractions}</td><td /><td /></tr>
            )}
          </tbody>
        </table>
        <table className="data self-start">
          <thead><tr><th>Most penalised</th><th className="r">Pen.</th><th className="r">PIM</th></tr></thead>
          <tbody>
            {d.top_takers.map((t) => (
              <tr key={t.player_id}><td>{t.name}</td><td className="r">{t.penalties}</td><td className="r">{t.pim}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-[0.68rem]" style={{ color: "var(--muted)" }}>
        League per GP: how often a team commits that infraction across all MD1 games this season. With {d.games} games,
        a gap of a few penalties is not yet a tendency.
      </p>
    </Section>
  );
}

/** A player who also dressed for another MD1 team is ranked on all their MD1 points. */
const movedTeams = (p: Scorer): boolean => typeof p.md1_points === "number" && p.md1_points !== p.p;

/** "3rd", or "T-11th" when another MD1 skater has as many points; "*" when the rank counts
    points for another MD1 team too. */
const md1Rank = (p: Scorer): string =>
  p.md1_rank ? `${p.md1_tied ? "T-" : ""}${ordinal(p.md1_rank)}${movedTeams(p) ? "*" : ""}` : "–";

function ScoringSection({ cur }: { cur: SeasonBlock }) {
  const s = cur.scoring;
  return (
    <Section title="Scoring" n={`${s.games} GP · ${s.goals} goals`}>
      <table className="data">
        <thead><tr><th>Player</th><th className="r">GP</th><th className="r">G</th><th className="r">A</th><th className="r">P</th><th className="r">MD1</th><th className="r">PP goals</th></tr></thead>
        <tbody>
          {s.top.map((p) => (
            <tr key={p.player_id}><td>{p.name}</td><td className="r">{p.gp}</td><td className="r">{p.g}</td>
              <td className="r">{p.a}</td><td className="r">{p.p}</td><td className="r">{md1Rank(p)}</td>
              <td className="r">{p.pp_goals}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[0.68rem]" style={{ color: "var(--ink-2)" }}>
        Top 3 scorers: {pct(s.top3_goal_share, 0)} of the goals. Power-play goals: {pct(s.pp_goal_share, 0)} of the goals.
        MD1: points rank among the {s.md1_skaters} MD1 skaters.
        {s.top.filter(movedTeams).map((p) => (
          <span key={p.player_id}> * {p.name}: ranked on {p.md1_points} MD1 points, {p.p} of them for this team.</span>
        ))}{" "}
        No ice-time data exists, so these are totals, not ratings.
      </p>
    </Section>
  );
}

function GoalieSection({ cur }: { cur: SeasonBlock }) {
  const g = cur.goaltending;
  return (
    <Section title="Goaltending" n={`${g.games} GP`}>
      <table className="data">
        <thead><tr><th>Goalie</th><th className="r">GP</th><th className="r">GS</th><th className="r">Min</th>
          <th className="r">SV %</th><th className="r">GAA</th><th className="r">Replaced</th></tr></thead>
        <tbody>
          {g.goalies.map((x) => (
            <tr key={x.player_id}><td>{x.name}</td><td className="r">{x.gp}</td><td className="r">{x.starts}</td>
              <td className="r">{x.minutes}</td><td className="r">{x.sv_pct === null ? "–" : x.sv_pct.toFixed(3).replace(/^0/, "")}</td>
              <td className="r">{dec(x.gaa)}</td><td className="r">{x.replaced}</td></tr>
          ))}
        </tbody>
      </table>
      {g.recent_starts.length > 0 && (
        <>
          <div className="mb-0.5 mt-2 text-[0.68rem]" style={{ color: "var(--muted)" }}>
            Who started, most recent first
          </div>
          <table className="data">
            <tbody>
              {g.recent_starts.map((s) => (
                <tr key={s.game_id}>
                  <td>{s.weekday} {shortDate(s.date)}</td><td>{s.home ? "home" : "away"}</td><td>{s.goalie}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Section>
  );
}

function HeadToHeadSection({ report }: { report: ScoutingReport }) {
  if (!report.head_to_head.length) return null;
  return (
    <Section title={`${report.team} vs Cincinnati`} n={`${report.head_to_head.length} games, all seasons`}>
      <table className="data">
        <thead><tr><th>Season</th><th>Date</th><th></th><th className="r">Score</th><th>{report.team.split(" ").slice(-1)[0]}</th><th>Venue</th></tr></thead>
        <tbody>
          {report.head_to_head.map((g) => (
            <tr key={g.game_id}><td>{g.season}</td><td>{shortDate(g.date)}</td><td>{g.home ? "home" : "away"}</td>
              <td className="r">{g.gf}-{g.ga}</td><td>{g.result}</td><td>{g.venue}</td></tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

export function ReportPage({ report, freshness }: { report: ScoutingReport; freshness?: string }) {
  const cur = report.current;
  return (
    <article className="sheet mx-auto max-w-[8.5in] p-6 shadow-sm" style={{ background: "var(--surface)" }}>
      <SheetHeader kicker={`Scouting report · ${cur.season} · ${report.division}`} title={report.team} right={<>
        {report.next_games.length > 0 && (
          <div>Next: {report.next_games.slice(0, 3).map((g) => `${shortDate(g.date)} ${g.home ? "vs" : "@"} ${g.opponent}`).join(" · ")}</div>
        )}
        <div>Built {shortDate(report.built_at.slice(0, 10))} from {cur.record.gp} completed games</div>
      </>} />

      <Banner cur={cur} />
      <NationalBlock national={report.national} />
      <Headline cur={cur} prior={report.prior} />
      <StrengthsChart ranks={cur.ranks} gp={cur.record.gp} />

      <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <ResultsSection cur={cur} />
        <PeriodSection cur={cur} />
      </div>
      <SpecialTeamsSection cur={cur} prior={report.prior} />
      <DisciplineSection cur={cur} />
      <Disclaimer page="1 of 2" team={report.team} freshness={freshness} />

      <div className="page-break" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <ScoringSection cur={cur} />
        <GoalieSection cur={cur} />
      </div>
      <HeadToHeadSection report={report} />
      <Disclaimer page="2 of 2" team={report.team} freshness={freshness} />
    </article>
  );
}

