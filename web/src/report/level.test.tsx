import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { National, NationalRank, ScoutingReport } from "../types/report";
import { NationalBlock, StrengthsChart, rankText, strengthLabel } from "./level";
import { recorded, withRecordedData } from "../testData";

const kentucky = recorded<unknown>("kentucky");

const report = kentucky as unknown as ScoutingReport;
const rank = (over: Partial<NationalRank>): NationalRank =>
  ({ team_id: 1, team: "T", rank: 9, source: "rebuilt", of: 79, games: 12, ...over });

afterEach(cleanup);

withRecordedData(() => {
  describe("the national rank", () => {
    it("says where each rank comes from and what it rests on", () => {
      expect(rankText(rank({}), null)).toBe("9th of 79 (rebuilt) · from 12 MD1 games");
      expect(rankText(rank({ rank: 30, source: "acha", of: 2, games: 39 }), "2026-11-04"))
        .toBe("30th of 2 (ACHA, Nov 4) · from 39 MD1 games");
      expect(rankText(rank({ rank: null, source: null, games: 0 }), null)).toBe("not ranked yet");
    });

    it("shows the team beside UC and the tournament line", () => {
      const { container } = render(<NationalBlock national={report.national} />);
      const text = container.textContent ?? "";
      expect(text).toContain("9th");
      expect(text).toContain("of 79 (rebuilt)");
      expect(text).not.toContain("9th 9th");
      expect(text).not.toMatch(/9th\s*9th of 79/);
      expect(text).toContain("Cincinnati");
      expect(text).toContain("41st");
      expect(screen.getByText(/top 24/)).toBeTruthy();
    });

    it("sets both ranks the same way, at the line's own size", () => {
      const { container } = render(<NationalBlock national={report.national} />);
      const ranks = [...container.querySelectorAll("[data-rank]")];
      expect(ranks.map((r) => r.textContent)).toEqual(["9th", "41st"]);
      expect(new Set(ranks.map((r) => r.className)).size).toBe(1);
      expect(container.querySelector(".text-lg")).toBeNull();
    });

    it("says a team with no MD1 game is not ranked yet, with no number", () => {
      const idle: National = { ...report.national, team: rank({ rank: null, source: null, games: 0 }) };
      const { container } = render(<NationalBlock national={idle} />);
      expect(container.textContent).toContain("not ranked yet");
      expect(container.textContent).not.toContain("of 79 (rebuilt) · from 0");
    });

    it("shows only the team on UC's own report", () => {
      const own: National = { ...report.national, uc: null };
      const { container } = render(<NationalBlock national={own} />);
      expect(container.textContent).not.toContain("Cincinnati");
    });
  });

  describe("strengths and weaknesses", () => {
    const ranks = report.current.ranks;

    it("labels only the top and bottom quarters", () => {
      expect(strengthLabel(16, 64)).toBe("Strength");
      expect(strengthLabel(17, 64)).toBeNull();
      expect(strengthLabel(48, 64)).toBeNull();
      expect(strengthLabel(49, 64)).toBe("Weakness");
    });

    it("lists every ranked area, best first", () => {
      const { container } = render(<StrengthsChart ranks={ranks} gp={12} />);
      const rows = [...container.querySelectorAll("[data-metric]")];
      expect(rows.length).toBe(6);
      expect(rows[0].textContent).toContain("17th of 80");
      expect(rows[0].textContent).toContain("Strength");
      expect(rows[5].textContent).toContain("72nd of 80");
      expect(rows[5].textContent).toContain("Weakness");
      expect(rows[2].textContent).not.toMatch(/Strength|Weakness/);
    });

    it("skips an area with no rank", () => {
      const some = ranks.map((r, i) => (i === 4 ? { ...r, rank: null, value: null } : r));
      const { container } = render(<StrengthsChart ranks={some} gp={12} />);
      expect(container.querySelectorAll("[data-metric]").length).toBe(5);
      expect(container.textContent).not.toContain("NaN");
    });

    it("says it's too early when nothing is ranked", () => {
      const none = ranks.map((r) => ({ ...r, rank: null }));
      const { container } = render(<StrengthsChart ranks={none} gp={3} />);
      expect(container.querySelectorAll("[data-metric]").length).toBe(0);
      expect(container.textContent).toContain("Too early to rank: fewer than 5 games.");
    });
  });

  describe("the chart caption", () => {
    it("counts the largest pool, not whichever area sorts first", () => {
      const ranks = report.current.ranks.map((r, i) => (i === 3 ? { ...r, rank: 1, of: 79 } : r));
      const { container } = render(<StrengthsChart ranks={ranks} gp={12} />);
      expect(container.textContent).toContain("among the 80 teams");
    });
  });
});
