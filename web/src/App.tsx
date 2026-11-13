import { useEffect, useState } from "react";
import { type DataIndex, dataUrl, freshnessText } from "./data";
import { UC, landingTeam, urlFor } from "./landing";
import { RankingsPage } from "./report/RankingsPage";
import { ReportPage } from "./report/ReportPage";
import { SpecialTeamsPage } from "./report/SpecialTeamsPage";
import type { TeamDetailReport } from "./types/detail";
import type { RankingsReport } from "./types/rankings";
import type { ScoutingReport } from "./types/report";

interface TeamSummary {
  team_id: number;
  team: string;
  division: string;
}

type View = "scouting" | "special-teams" | "rankings";
const VIEWS: View[] = ["scouting", "special-teams", "rankings"];
const TITLES: Record<View, string> = { scouting: "Scouting", "special-teams": "Special teams", rankings: "Where we stand" };

const fromUrl = (): { team: number | null; view: View; season: number | null } => {
  const params = new URLSearchParams(window.location.search);
  const id = Number(params.get("team"));
  const season = Number(params.get("season"));
  return {
    // No team asked for: resolved to UC's next opponent once the team list loads.
    team: Number.isInteger(id) && id > 0 ? id : null,
    view: VIEWS.find((v) => v === params.get("view")) ?? "scouting",
    // Optional `?season=60`: an earlier season's pages, e.g. to print-check a full season.
    season: Number.isInteger(season) && season > 0 ? season : null,
  };
};

export function App() {
  const initial = fromUrl();
  const [index, setIndex] = useState<DataIndex | null>(null);
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [teamId, setTeamId] = useState<number | null>(initial.team);
  // The team was chosen for the visitor (UC's next opponent), not picked: kept out of the URL.
  const [autoTeam, setAutoTeam] = useState(initial.team === null);
  const [view, setView] = useState<View>(initial.view);
  const [report, setReport] = useState<ScoutingReport | null>(null);
  const [detail, setDetail] = useState<TeamDetailReport | null>(null);
  const [rankings, setRankings] = useState<RankingsReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Without ?season=, the season the data was last built for.
  const season = initial.season ?? index?.current_season ?? null;
  const freshness = index ? freshnessText(index) : undefined;

  useEffect(() => {
    fetch("/data/index.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`index: HTTP ${r.status}`))))
      .then((data: DataIndex) => {
        setIndex(data);
        // Read by the printed footer of the flowing sheets (`@page flow` in index.css).
        document.documentElement.style.setProperty("--print-freshness", JSON.stringify(freshnessText(data)));
      })
      .catch((e: Error) => setError(`${e.message}. Has the data been exported (bca export static)?`));
  }, []);

  useEffect(() => {
    if (index === null || season === null) return;
    if (!index.seasons.includes(season)) {
      setError(`No pages for season ${season}.`);
      return;
    }
    fetch(dataUrl("teams", season))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`teams: HTTP ${r.status}`))))
      .then(setTeams)
      .catch((e: Error) => setError(e.message));
  }, [index, season]);

  useEffect(() => {
    if (teamId !== null || teams.length === 0 || season === null) return;
    fetch(dataUrl("scouting", season, UC))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`scouting: HTTP ${r.status}`))))
      .then((uc: ScoutingReport) => setTeamId(landingTeam(uc, teams.map((t) => t.team_id))))
      .catch(() => setTeamId(UC));
  }, [teamId, teams]);

  useEffect(() => {
    // Only for a season with pages: otherwise its 404 would replace "No pages for season N."
    if (teamId === null || season === null || index === null || !index.seasons.includes(season)) return;
    window.history.replaceState(null, "", urlFor(window.location.href, teamId, view, autoTeam));

    // The rankings page is league-wide and always UC's: it ignores the team.
    const path = view === "rankings" ? dataUrl("rankings", season) : dataUrl(view, season, teamId);
    fetch(path)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${view}: HTTP ${r.status}`))))
      .then((data: ScoutingReport | TeamDetailReport | RankingsReport) => {
        setError(null);
        if (view === "scouting") setReport(data as ScoutingReport);
        else if (view === "special-teams") setDetail(data as TeamDetailReport);
        else setRankings(data as RankingsReport);
        document.title = `${TITLES[view]}: ${"team" in data ? data.team : data.season}`;
      })
      .catch((e: Error) => setError(e.message));
  }, [teamId, view, autoTeam, season, index]);

  const tab = (name: View, label: string) => (
    <button type="button" onClick={() => setView(name)} className="rounded px-3 py-1"
            aria-pressed={view === name}
            style={view === name
              ? { background: "var(--band)", color: "var(--band-ink)" }
              : { background: "var(--surface)", color: "var(--ink)", border: "1px solid var(--axis)" }}>
      {label}
    </button>
  );

  return (
    <div className="min-h-screen py-4">
      <div className="no-print mx-auto mb-3 flex max-w-[8.5in] flex-wrap items-center gap-2 px-2 text-sm">
        {/* Where we stand is always UC's: a team picker there reads as if the page were that
            team's. The pick is kept for the other tabs. */}
        {view !== "rankings" && (<>
          <label htmlFor="team" className="font-semibold">Team</label>
          <select id="team" className="rounded border px-2 py-1"
                  style={{ background: "var(--surface)", borderColor: "var(--axis)", color: "var(--ink)" }}
                  value={teamId ?? ""} onChange={(e) => { setAutoTeam(false); setTeamId(Number(e.target.value)); }}>
            {teams.map((t) => (
              <option key={t.team_id} value={t.team_id}>{t.team} ({t.division.replace(/^MD1 /, "")})</option>
            ))}
          </select>
        </>)}
        {tab("scouting", "Scouting report")}
        {tab("special-teams", "Special teams & discipline")}
        {tab("rankings", "Where we stand")}
        <button type="button" onClick={() => window.print()} className="ml-auto rounded px-3 py-1 font-semibold"
                style={{ background: "var(--accent)", color: "#fff" }}>Print</button>
      </div>
      {error && <p className="no-print mx-auto max-w-[8.5in] px-2 text-sm" style={{ color: "var(--accent)" }}>{error}</p>}
      {view === "scouting" && report?.team_id === teamId && <ReportPage report={report} freshness={freshness} />}
      {view === "special-teams" && detail?.team_id === teamId && <SpecialTeamsPage report={detail} freshness={freshness} />}
      {view === "rankings" && rankings && (
        <RankingsPage report={rankings} freshness={freshness}
                      teamHref={(id) => `?team=${id}${initial.season ? `&season=${initial.season}` : ""}`} />
      )}
    </div>
  );
}
