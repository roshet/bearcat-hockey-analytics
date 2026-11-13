import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { TeamDetailReport } from "../types/detail";
import { SpecialTeamsPage, leagueCountSentence } from "./SpecialTeamsPage";
import { recorded, withRecordedData } from "../testData";

const kentuckyDetail = recorded<unknown>("kentucky-detail");
const ucDetail = recorded<unknown>("uc-detail");

// Real payloads from `bca scouting build` (2026-27).
const uc = ucDetail as unknown as TeamDetailReport;
const kentucky = kentuckyDetail as unknown as TeamDetailReport;

afterEach(cleanup);

withRecordedData(() => {
  describe("SpecialTeamsPage", () => {
    it("renders UC's page with its derived numbers", () => {
      render(<SpecialTeamsPage report={uc} />);
      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("University of Cincinnati");
      expect(screen.getByText("16.0%")).toBeTruthy(); // PP 8/50
      expect(screen.getByText(/Players are shown by jersey number/)).toBeTruthy();
      // The banner holds only the sample and the numbers rule; explanations sit by their numbers.
      expect(screen.getByText(/league's box scores list 34 of these 50/).closest("section")).toBeTruthy();
      expect(screen.getByText("Power-play goals against")).toBeTruthy();
    });

    it("never shows a UC player's name", () => {
      render(<SpecialTeamsPage report={uc} />);
      const players = uc.current.players.map((p) => p.player);
      expect(players.length).toBeGreaterThan(0);
      expect(players.every((p) => p.startsWith("#"))).toBe(true);
    });

    it("names players on an opponent's page", () => {
      render(<SpecialTeamsPage report={kentucky} />);
      expect(screen.queryByText(/Players are shown by jersey number/)).toBeNull();
      const named = kentucky.current.players.find((p) => !p.player.startsWith("#"));
      expect(named).toBeDefined();
      expect(screen.getAllByText(named!.player).length).toBeGreaterThan(0);
    });

    it("lists one game-log row per game and flows instead of fixing two pages", () => {
      const { container } = render(<SpecialTeamsPage report={uc} />);
      // One on-screen disclaimer; in print, the `@page flow` footer repeats it on every page.
      expect(screen.getAllByText(/not affiliated with or endorsed by/).length).toBe(1);
      expect(container.querySelector(".page-break")).toBeNull();
      expect(container.querySelector("article")?.classList.contains("flow")).toBe(true);
      expect(document.documentElement.style.getPropertyValue("--print-team")).toBe('"University of Cincinnati"');
      expect(screen.getAllByText(/^(vs|@)$/).length).toBe(uc.current.game_log.length);
    });
  });

  describe("leagueCountSentence", () => {
    const base = uc.current.summary;

    it("says how far the league's own count is from ours", () => {
      const s = { ...base, pp_goals: 8, pp_opportunities: 50, pp_pct: 0.16,
                  league_pp_opportunities: 34, games_not_entered: 3, games_counted_differently: 3 };
      expect(leagueCountSentence(s)).toBe(
        "The league's box scores list 34 of these 50 power plays (3 games never entered, 3 counted " +
        "differently), so a power-play % worked out from the league site reads 24%, not 16%.");
    });

    it("is silent when the counts agree, and leaves out a reason that is zero", () => {
      expect(leagueCountSentence({ ...base, pp_opportunities: 20, league_pp_opportunities: 20 })).toBeNull();
      expect(leagueCountSentence({ ...base, pp_goals: 2, pp_opportunities: 10, pp_pct: 0.2,
                                        league_pp_opportunities: 0, games_not_entered: 1,
                                        games_counted_differently: 0 }))
        .toBe("The league's box scores list 0 of these 10 power plays (1 game never entered).");
    });
  });
});
