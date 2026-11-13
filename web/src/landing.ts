import type { ScoutingReport } from "./types/report";

export const UC = 265;

/** Where the app opens with no `?team=`: UC's next MD1 opponent, so the first page a coach
    sees is a scouting report, not a report card on UC. UC when there is none (off-season,
    or the next opponents aren't MD1 teams with a report). */
export function landingTeam(uc: Pick<ScoutingReport, "next_games">, teamIds: number[]): number {
  const known = new Set(teamIds);
  return uc.next_games.find((g) => known.has(g.opponent_id))?.opponent_id ?? UC;
}

/** The address to show for a team and tab. While the team is the automatic choice it is
    left out, so a bookmark of the front page keeps following the next opponent; a team
    someone picked is written in, so that bookmark stays on it. */
export function urlFor(href: string, team: number, view: string, auto: boolean): string {
  const url = new URL(href);
  if (auto) url.searchParams.delete("team");
  else url.searchParams.set("team", String(team));
  if (view === "scouting") url.searchParams.delete("view");
  else url.searchParams.set("view", view);
  return url.toString();
}
