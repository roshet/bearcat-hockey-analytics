import { useEffect } from "react";
import { dec, pct, rate, shortDate } from "../format";
import type { DetailSeason, TeamDetailReport } from "../types/detail";
import { Disclaimer, Section, SheetHeader, Tile, rankOf } from "./parts";

/* Special teams & discipline: the game-by-game detail behind a team's power
   play and penalty kill. Print-first, like the scouting report. No trend line
   and no per-minute split: at this sample both would show luck, not tendency.
   Its lists grow with the season (10 game rows in October, 37 by March), so the
   sheet flows from page to page instead of fixing what goes on each. */

/** Keeps page 2 to one sheet: the rest are summed up in one line (all are in the data). */
const MAX_PLAYERS = 14;

const range = (r: unknown): string => {
  const pair = r as [number, number] | null;
  return pair ? `likely ${Math.round(pair[0] * 100)}–${Math.round(pair[1] * 100)}%` : "";
};

function Headline({ cur, prior }: { cur: DetailSeason; prior?: DetailSeason | null }) {
  const s = cur.summary;
  const last = (fn: (b: DetailSeason) => string) => (prior ? `${prior.season}: ${fn(prior)}` : undefined);
  return (
    <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-6">
      <Tile label={`Power play (${s.pp_goals}/${s.pp_opportunities})`} value={pct(s.pp_pct)}
            rank={rankOf(cur.ranks, "power play %")}
            sub={[range(s.pp_range), last((b) => pct(b.summary.pp_pct))].filter(Boolean).join(" · ")} />
      <Tile label={`Penalty kill (${s.pk_opportunities - s.pp_goals_against}/${s.pk_opportunities})`}
            value={pct(s.pk_pct)} rank={rankOf(cur.ranks, "penalty kill %")}
            sub={[range(s.pk_range), last((b) => pct(b.summary.pk_pct))].filter(Boolean).join(" · ")} />
      <Tile label="PP minutes / game" value={dec(s.pp_minutes_per_game, 1)}
            sub={last((b) => dec(b.summary.pp_minutes_per_game, 1))} />
      <Tile label="Short-handed min / game" value={dec(s.sh_minutes_per_game, 1)}
            rank={rankOf(cur.ranks, "short-handed minutes per game")}
            sub={last((b) => dec(b.summary.sh_minutes_per_game, 1))} />
      <Tile label="Minors / game" value={dec(cur.minors_per_game, 1)}
            rank={rankOf(cur.ranks, "minors per game")} sub={last((b) => dec(b.minors_per_game, 1))} />
      <Tile label="SH goals for / against" value={`${s.sh_goals_for} / ${s.sh_goals_against}`}
            sub={last((b) => `${b.summary.sh_goals_for} / ${b.summary.sh_goals_against}`)} />
    </div>
  );
}

/** The league's own PP count, only when it differs: a PP% worked out from the league site
    uses it, so say how far it is from ours and why. Shown under the game log, beside the
    PP column it differs from. */
export function leagueCountSentence(s: DetailSeason["summary"]): string | null {
  if (s.league_pp_opportunities === s.pp_opportunities) return null;
  const why = [
    s.games_not_entered ? `${s.games_not_entered} ${s.games_not_entered === 1 ? "game" : "games"} never entered` : "",
    s.games_counted_differently
      ? `${s.games_counted_differently} counted differently` : "",
  ].filter(Boolean).join(", ");
  const theirs = s.league_pp_opportunities ? pct(s.pp_goals / s.league_pp_opportunities, 0) : null;
  return `The league's box scores list ${s.league_pp_opportunities} of these ${s.pp_opportunities} power plays` +
    (why ? ` (${why})` : "") +
    (theirs ? `, so a power-play % worked out from the league site reads ${theirs}, not ${pct(s.pp_pct, 0)}.` : ".");
}

function Banner({ report }: { report: TeamDetailReport }) {
  const cur = report.current;
  return (
    <div className="keep my-2 rounded px-2 py-1 text-[0.72rem] leading-snug"
         style={{ background: "var(--notice)", color: "var(--notice-ink)" }}>
      <strong>Sample: {cur.games} games, {cur.summary.games} with special teams.</strong>
      {report.players_as_numbers && <> Players are shown by jersey number.</>}
    </div>
  );
}

function GameLog({ cur }: { cur: DetailSeason }) {
  // Notes now only explain a missing or partial box score; most teams have none.
  const notes = cur.game_log.some((g) => g.flag);
  return (
    <Section title="Game by game" n={`${cur.games} GP`} split>
      <table className="data">
        <thead><tr><th>Date</th><th></th><th>Opponent</th><th className="r">Score</th>
          <th className="r">PP</th><th className="r">PP min</th><th className="r">PK (GA/opp.)</th>
          <th className="r">SH min</th><th className="r">Minors</th>{notes && <th>Note</th>}</tr></thead>
        <tbody>
          {cur.game_log.map((g) => (
            <tr key={g.game_id}>
              <td>{shortDate(g.date)}</td><td>{g.home ? "vs" : "@"}</td><td>{g.opponent}</td>
              <td className="r">{g.gf}-{g.ga} {g.result}</td>
              {g.simulated ? (
                <>
                  <td className="r">{g.pp_goals}/{g.pp_opportunities}</td><td className="r">{dec(g.pp_minutes, 1)}</td>
                  <td className="r">{g.pp_goals_against}/{g.pk_opportunities}</td><td className="r">{dec(g.pk_minutes, 1)}</td>
                  <td className="r">{g.minors}</td>
                </>
              ) : (
                <td colSpan={5} className="r" style={{ color: "var(--muted)" }}>not available</td>
              )}
              {notes && <td className="text-[0.68rem]" style={{ color: "var(--muted)" }}>{g.flag ?? ""}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      {leagueCountSentence(cur.summary) && (
        <p className="mt-1 text-[0.68rem]" style={{ color: "var(--muted)" }}>{leagueCountSentence(cur.summary)}</p>
      )}
    </Section>
  );
}

function PowerPlayGoalsAgainst({ cur }: { cur: DetailSeason }) {
  return (
    <Section title="Power-play goals against" n={`${cur.pp_goals_against.length} goals`} split>
      {cur.pp_goals_against.length === 0 ? <p className="text-xs">None.</p> : (
        <table className="data">
          <thead><tr><th>Date</th><th>Opponent</th><th>Per.</th><th className="r">Time</th>
            <th>Strength</th><th>Penalty being served</th></tr></thead>
          <tbody>
            {cur.pp_goals_against.map((g) => (
              <tr key={`${g.game_id}-${g.period}-${g.clock}`}>
                <td>{shortDate(g.date)}</td><td>{g.opponent}</td><td>{g.period}</td>
                <td className="r">{g.clock}</td><td>{g.strength}</td>
                <td>{g.serving.map((p) => `${p.infraction} (${p.player})`).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

function ShortHandedGoals({ report }: { report: TeamDetailReport }) {
  const goals = report.current.sh_goals;
  const scored = goals.filter((g) => g.scored_by_team).length;
  return (
    <Section title={`Short-handed goals: ${scored} for, ${goals.length - scored} against`}
             n={`${goals.length} goals`}>
      {goals.length === 0 ? <p className="text-xs">None.</p> : (
        // Two columns of one-line entries: a team can have 15+ in a dozen games.
        <ul className="columns-2 gap-6 text-[0.75rem] leading-snug">
          {goals.map((g) => (
            <li key={`${g.game_id}-${g.period}-${g.clock}`} className="num break-inside-avoid border-b py-0.5"
                style={{ borderColor: "var(--hairline)" }}>
              <strong>{g.scored_by_team ? "For" : "Against"}</strong> · {shortDate(g.date)} {g.opponent} · P{g.period}{" "}
              {g.clock}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function Discipline({ cur }: { cur: DetailSeason }) {
  return (
    <Section title="Discipline" n={`${cur.games} GP`}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <table className="data self-start">
          <thead><tr><th>Infraction</th><th className="r">Count</th><th className="r">Per GP</th><th className="r">League per GP</th></tr></thead>
          <tbody>
            {cur.infractions.map((i) => (
              <tr key={i.infraction}><td>{i.infraction}</td><td className="r">{i.count}</td>
                <td className="r">{rate(i.per_game)}</td><td className="r" style={{ color: "var(--ink-2)" }}>{rate(i.league_per_game)}</td></tr>
            ))}
            {cur.other_infractions > 0 && (
              <tr><td style={{ color: "var(--muted)" }}>Everything else</td><td className="r">{cur.other_infractions}</td><td /><td /></tr>
            )}
          </tbody>
        </table>
        <table className="data self-start">
          <thead><tr><th>Player</th><th className="r">Pen.</th><th className="r">Minors</th><th className="r">PIM</th>
            <th className="r">SH min caused</th></tr></thead>
          <tbody>
            {cur.players.slice(0, MAX_PLAYERS).map((p) => (
              <tr key={p.player}><td>{p.player}</td><td className="r">{p.penalties}</td><td className="r">{p.minors}</td>
                <td className="r">{p.pim}</td><td className="r">{dec(p.sh_minutes_caused, 1)}</td></tr>
            ))}
            {cur.players.length > MAX_PLAYERS && (
              <tr><td colSpan={5} style={{ color: "var(--muted)" }}>
                + {cur.players.length - MAX_PLAYERS} more players with{" "}
                {Math.max(...cur.players.slice(MAX_PLAYERS).map((p) => p.penalties))} or fewer penalties
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-[0.68rem]" style={{ color: "var(--muted)" }}>
        "SH min caused": minutes that player's penalties had the team short-handed (at 5-on-3, both penalties count).
        Goals scored meanwhile are left out: the player is in the box, so they measure the penalty kill. With{" "}
        {cur.games} games, a difference of a penalty or two is not a tendency.
      </p>
    </Section>
  );
}

export function SpecialTeamsPage({ report, freshness }: { report: TeamDetailReport; freshness?: string }) {
  const cur = report.current;
  useEffect(() => {
    // Read by the printed page footer (`@page flow` in index.css).
    document.documentElement.style.setProperty("--print-team", JSON.stringify(report.team));
  }, [report.team]);
  return (
    <article className="sheet flow mx-auto max-w-[8.5in] p-6 shadow-sm" style={{ background: "var(--surface)" }}>
      <SheetHeader kicker={`Special teams & discipline · ${cur.season} · ${report.division}`} title={report.team}
                   right={<div>Built {shortDate(report.built_at.slice(0, 10))} from {cur.games} completed games</div>} />
      <Banner report={report} />
      <Headline cur={cur} prior={report.prior} />
      <p className="mt-0.5 text-[0.62rem]" style={{ color: "var(--muted)" }}>
        MD1 rank: 1 = best (for short-handed minutes and minors, 1 = fewest), among the {cur.ranks[0]?.of ?? 0} MD1
        teams with 5+ games this season. "Likely": how much a rate could move on a sample this size. Power plays and
        their minutes are rebuilt from the penalty log; the league publishes no PP time.
      </p>
      <div className="mt-3"><GameLog cur={cur} /></div>
      <PowerPlayGoalsAgainst cur={cur} />
      <ShortHandedGoals report={report} />
      <Discipline cur={cur} />
      <Disclaimer team={report.team} freshness={freshness} />
    </article>
  );
}
