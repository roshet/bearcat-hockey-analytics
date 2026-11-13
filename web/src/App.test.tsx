import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import css from "./index.css?raw";
import { DISCLAIMER } from "./report/parts";
import { recorded, withRecordedData } from "./testData";

const kentucky = recorded<Record<string, unknown>>("kentucky");
const rankings = recorded<unknown>("rankings-73");

// UC's report only needs its upcoming games; Illinois (411) and Kentucky reuse the
// Kentucky payload under their own ids, which is all the page needs to render.
const teams = [
  { team_id: 265, team: "University of Cincinnati", division: "MD1 SECHL" },
  { team_id: 346, team: "University of Kentucky", division: "MD1 SECHL" },
  { team_id: 411, team: "University of Illinois", division: "MD1 CSCHL" },
];
const uc = { ...kentucky, team_id: 265, team: "University of Cincinnati",
             next_games: [{ game_id: 1, date: "2026-10-24", home: true, opponent_id: 411,
                            opponent: "University of Illinois" }] };
const index = { current_season: 73, seasons: [60, 73], built_at: "2026-10-24T11:00:00+00:00",
                data_through: "2026-10-22" };
// The deployed site is static: the app reads the files `bca export static` writes.
const responses: Record<string, unknown> = {
  "/data/index.json": index,
  "/data/73/teams.json": teams,
  "/data/73/scouting/265.json": uc,
  "/data/73/scouting/411.json": { ...kentucky, team_id: 411, team: "University of Illinois" },
  "/data/73/scouting/346.json": kentucky,
  "/data/73/rankings.json": rankings,
};

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    const body = responses[path];
    return { ok: body !== undefined, status: body ? 200 : 404, json: async () => body };
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

withRecordedData(() => {
  describe("App front door", () => {
    it("opens on UC's next opponent without writing it into the address", async () => {
      render(<App />);
      expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("University of Illinois");
      expect(window.location.search).toBe("");
    });

    it("writes a team once someone picks it", async () => {
      render(<App />);
      await screen.findByRole("heading", { level: 1 });
      fireEvent.change(screen.getByLabelText("Team"), { target: { value: "346" } });
      expect((await screen.findByText("University of Kentucky", { selector: "h1" })).textContent)
        .toBe("University of Kentucky");
      expect(window.location.search).toBe("?team=346");
    });
  });

  describe("App views", () => {
    it("opens the rankings view from ?view=rankings", async () => {
      window.history.replaceState(null, "", "/?view=rankings");
      render(<App />);
      expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("The road to nationals");
      expect(screen.getByRole("button", { name: "Where we stand" }).getAttribute("aria-pressed")).toBe("true");
      expect(window.location.search).toBe("?view=rankings");
    });

    it("hides the team picker on Where we stand, which is always about UC, and keeps the pick", async () => {
      window.history.replaceState(null, "", "/?team=346&view=rankings");
      render(<App />);
      await screen.findByText("The road to nationals");
      expect(screen.queryByLabelText("Team")).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Scouting report" }));
      expect((await screen.findByText("University of Kentucky", { selector: "h1" })).textContent)
        .toBe("University of Kentucky");
      expect((screen.getByLabelText("Team") as HTMLSelectElement).value).toBe("346");
    });
  });

  describe("App data", () => {
    it("shows a clear message for a season with no pages", async () => {
      window.history.replaceState(null, "", "/?season=10");
      render(<App />);
      expect(await screen.findByText("No pages for season 10.")).toBeTruthy();
    });

    it("keeps that message when a team is named too", async () => {
      window.history.replaceState(null, "", "/?season=10&team=346");
      // The report's 404 arrives after the index, as it can on a real network.
      vi.stubGlobal("fetch", vi.fn(async (path: string) => {
        if (path !== "/data/index.json") await new Promise((r) => setTimeout(r, 30));
        const body = responses[path];
        return { ok: body !== undefined, status: body ? 200 : 404, json: async () => body };
      }));
      render(<App />);
      expect(await screen.findByText("No pages for season 10.")).toBeTruthy();
      await new Promise((r) => setTimeout(r, 60));
      expect(screen.queryByText(/HTTP 404/)).toBeNull();
      expect(screen.getByText("No pages for season 10.")).toBeTruthy();
    });

    it("shows when the data was updated", async () => {
      render(<App />);
      await screen.findByRole("heading", { level: 1 });
      expect(screen.getAllByText(/Data through Oct 22 · updated Oct 24/).length).toBeGreaterThan(0);
    });

    it("credits the ACHA and FloHockey on the page", async () => {
      render(<App />);
      await screen.findByRole("heading", { level: 1 });
      expect(screen.getAllByText(/Data courtesy of the ACHA and FloHockey/).length).toBeGreaterThan(0);
      expect(document.body.textContent).not.toContain("HockeyTech");
    });

    it("prints the same credit in the page footer", () => {
      expect(css).toContain(`content: "${DISCLAIMER}"`);
    });
  });
});
