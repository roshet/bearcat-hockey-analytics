import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ScoutingReport } from "../types/report";
import { ReportPage } from "./ReportPage";
import { recorded, withRecordedData } from "../testData";

const kentucky = recorded<unknown>("kentucky");

// A real payload from `bca scouting build --team 346` (2026-27, 12 GP).
const report = kentucky as unknown as ScoutingReport;

afterEach(cleanup);

withRecordedData(() => {
  describe("ReportPage on the Kentucky report", () => {
    it("renders the header, banner and headline numbers", () => {
      render(<ReportPage report={report} />);
      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("University of Kentucky");
      expect(screen.getByText(/Sample: 12 games\./)).toBeTruthy();
      expect(screen.getAllByText("10-2").length).toBeGreaterThan(0);
      // PP 19/70: in the headline tile and the special-teams table.
      expect(screen.getAllByText("27.1%").length).toBe(2);
    });

    it("names UC only in the head-to-head section", () => {
      const { container } = render(<ReportPage report={report} />);
      const h2h = screen.getByText("University of Kentucky vs Cincinnati").closest("section");
      expect(h2h).toBeTruthy();
      const outside = [...container.querySelectorAll("section")].filter((s) => s !== h2h);
      // Form lists UC as an opponent like any other; no section compares against UC.
      for (const section of outside) {
        expect(section.querySelector("h2")?.textContent ?? "").not.toMatch(/Cincinnati/);
      }
    });

    it("shows who started in net, most recent first", () => {
      render(<ReportPage report={report} />);
      expect(screen.getByText("Who started, most recent first")).toBeTruthy();
      expect(screen.getByText("Replaced")).toBeTruthy();
      expect(screen.queryByText("League context")).toBeNull();
      expect(screen.queryByText(/put the opponent a man up/)).toBeNull();
    });

    it("names the infractions, with no timing chart", () => {
      render(<ReportPage report={report} />);
      expect(screen.getByText("What they take")).toBeTruthy();
      expect(screen.getByText("Roughing")).toBeTruthy();
      expect(screen.queryByText(/When they take them/)).toBeNull();
    });

    it("carries the non-affiliation footer on both printed pages", () => {
      render(<ReportPage report={report} />);
      expect(screen.getAllByText(/not affiliated with or endorsed by/).length).toBe(2);
    });

    it("states the sample size on every section", () => {
      const { container } = render(<ReportPage report={report} />);
      for (const heading of container.querySelectorAll("section > h2")) {
        expect(heading.textContent).toMatch(/GP|games/);
      }
    });
  });

  describe("the ranks moved into the strengths chart", () => {
    it("no longer prints a rank inside the tiles", () => {
      const { container } = render(<ReportPage report={report} />);
      expect(container.textContent).not.toContain("MD1 rank 18th");
      expect(container.querySelectorAll("[data-metric]").length).toBe(6);
    });
  });

  describe("scorers against the league", () => {
    it("ranks each listed scorer's points across MD1, ties marked", () => {
      render(<ReportPage report={report} />);
      const table = screen.getByText("MD1").closest("table")!;
      expect(table.textContent).toContain("3rd");
      expect(table.textContent?.match(/T-11th/g)?.length).toBe(2);
      expect(screen.getByText(/among the 1450 MD1 skaters/)).toBeTruthy();
    });
  });

  describe("a scorer who also played for another MD1 team", () => {
    it("marks the rank and says what it is ranked on", () => {
      render(<ReportPage report={report} />);
      const top = report.current.scoring.top[0];
      const table = screen.getByText("MD1").closest("table")!;
      expect(table.textContent).toContain("3rd*");
      expect(document.body.textContent).toContain(`* ${top.name}: ranked on ${top.md1_points} MD1 points, ${top.p} of them for this team.`);
    });
  });
});
