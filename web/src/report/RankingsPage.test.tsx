import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { RankingsReport } from "../types/rankings";
import { RankingsPage, headlineRank } from "./RankingsPage";
import { recorded, withRecordedData } from "../testData";

const r60 = recorded<unknown>("rankings-60");
const r73 = recorded<unknown>("rankings-73");

// Real payloads from `bca rankings build`: 2026-27 before the ACHA's first computer ranking,
// and 2025-26 with four official rankings stored.
const now = r73 as unknown as RankingsReport;
const past = r60 as unknown as RankingsReport;

afterEach(cleanup);

const rankingRows = () => [...screen.getByRole("table", { name: "MD1 ranking" }).querySelectorAll("tbody tr")];
const rowFor = (_container: HTMLElement, team: string) =>
  rankingRows().find((tr) => tr.textContent?.includes(team)) as HTMLElement;

withRecordedData(() => {
  describe("RankingsPage", () => {
    it("draws the nationals line after the 24th row", () => {
      render(<RankingsPage report={now} />);
      const rows = rankingRows();
      const line = rows.findIndex((tr) => tr.textContent?.includes("Nationals line"));
      expect(line).toBe(24);
      expect(rows[line - 1].querySelector("td")?.textContent).toBe("24");
      expect(rows[line + 1].querySelector("td")?.textContent).toBe("25");
    });

    it("labels the rank as a rebuild before any official ranking", () => {
      render(<RankingsPage report={now} />);
      expect(screen.getByText("33rd")).toBeTruthy();
      expect(screen.getByText(/Rebuilt with games through Oct 22/)).toBeTruthy();
      expect(screen.queryByText(/ACHA ranking as of/)).toBeNull();
    });

    it("says no official ranking is published yet when checks is empty", () => {
      render(<RankingsPage report={now} />);
      expect(screen.getByText(/hasn't published a computer ranking for this season yet/)).toBeTruthy();
    });

    it("says how many places from the line", () => {
      render(<RankingsPage report={now} />);
      expect(screen.getByText("9 below")).toBeTruthy();
      const above = { ...now, uc: { ...now.uc!, places_from_line: 3 } };
      cleanup();
      render(<RankingsPage report={above} />);
      expect(screen.getByText("3 above")).toBeTruthy();
    });

    it("leads with the official rank and shows the rebuilt rank beside it", () => {
      render(<RankingsPage report={past} />);
      expect(screen.getByText(/ACHA ranking as of Mar 17/)).toBeTruthy();
      expect(screen.getByText(/rebuilt 39th with games through Mar 7/)).toBeTruthy();
      expect(headlineRank({ rank: 9, official_rank: 6 })).toEqual({ shown: 6, rebuilt: 9, official: true });
      expect(headlineRank({ rank: 27, official_rank: null })).toEqual({ shown: 27, rebuilt: 27, official: false });
    });

    it("marks a row whose official and rebuilt ranks differ by 3 or more", () => {
      const { container } = render(<RankingsPage report={past} />);
      const asu = rowFor(container, "Arizona State University");
      expect(asu.querySelector("td")?.textContent).toBe("26");
      expect(within(asu).getByTitle(/rebuilt 31st/)).toBeTruthy();
      // The row is the ACHA's: its A, B and average sit beside its rank, not ours.
      const adrian = [...rowFor(container, "Adrian College").querySelectorAll("td")].map((td) => td.textContent);
      expect([adrian[0], adrian[4], adrian[5], adrian[6]]).toEqual(["6", "5", "6", "5.5"]);
      const ohio = rowFor(container, "Ohio University");
      expect(within(ohio).queryByTitle(/rebuilt/)).toBeNull();
    });

    it("lists the remaining MD1 games with each opponent's rank and a link to its report", () => {
      render(<RankingsPage report={now} />);
      const links = screen.getAllByRole("link", { name: "University of Illinois" });
      expect(links[0].getAttribute("href")).toBe("?team=411");
      expect(screen.getByText(/18 MD1 games left/)).toBeTruthy();
    });

    it("states the base rate as x of y with its seasons", () => {
      render(<RankingsPage report={now} />);
      expect(screen.getByText(
        "Of 33 teams ranked 28th–38th on Oct 22 in 2023-24, 2024-25 and 2025-26, 6 finished in the top 24.",
      )).toBeTruthy();
    });

    it("withholds the base rate when it is null", () => {
      render(<RankingsPage report={past} />);
      expect(screen.queryByText(/finished in the top 24/)).toBeNull();
    });

    it("hides the UC block when uc is null", () => {
      render(<RankingsPage report={{ ...now, uc: null }} />);
      expect(screen.queryByText(/places from the line/i)).toBeNull();
      expect(screen.getAllByRole("row").length).toBeGreaterThan(80);
    });

    it("highlights UC only: the page is always UC's", () => {
      const { container } = render(<RankingsPage report={now} />);
      expect(rowFor(container, "University of Cincinnati").dataset.highlight).toBe("uc");
      expect(rowFor(container, "University of Illinois").dataset.highlight).toBeUndefined();
      expect(rowFor(container, "Ohio University").dataset.highlight).toBeUndefined();
    });

    it("lists the check against each official ranking", () => {
      render(<RankingsPage report={past} />);
      expect(screen.getByText(/Mar 17: 50 of 81 at the same rank, 78 within 2 places; same top 24/)).toBeTruthy();
      expect(screen.getByText(/Nov 2: .*different top 24/)).toBeTruthy();
    });

    it("marks only the differences the check found, with the rank rebuilt as of the PDF's date", () => {
      // The table and the check speak from one comparison, even when games were played since.
      const last = past.checks[past.checks.length - 1];
      const ohio = past.rows.find((r) => r.team === "Ohio University")!;
      const changed = {
        ...past,
        checks: [...past.checks.slice(0, -1), { ...last, differences: [
          { team_id: ohio.team_id, team: "MD1 Ohio University", official: 2, rebuilt: 5, cause: null },
        ] }],
      };
      const { container } = render(<RankingsPage report={changed} />);
      expect(within(rowFor(container, "Adrian College")).queryByTitle(/rebuilt/)).toBeNull();
      expect(within(rowFor(container, "Ohio University")).getByTitle("rebuilt 5th as of Mar 17")).toBeTruthy();
    });

    it("says when our rebuild stops before the official ranking's date", () => {
      render(<RankingsPage report={past} />);
      expect(screen.getByText(/Mar 17: 50 of 81 .* \(our games through Mar 7\)/)).toBeTruthy();
      expect(screen.getByText(/Feb 1: 59 of 81 [^(]*$/)).toBeTruthy();
    });

    it("lists a team the ACHA hasn't ranked after the official ranks, and keeps the line under its 24th", () => {
      const ky = past.rows.find((r) => r.team === "University of Kentucky")!;
      const unranked = { ...past, rows: past.rows.map((r) => r.team_id === ky.team_id
        ? { ...r, official_rank: null, official_rank_a: null, official_rank_b: null, official_avg: null } : r) };
      render(<RankingsPage report={unranked} />);
      const rows = rankingRows();
      const line = rows.findIndex((tr) => tr.textContent?.includes("Nationals line"));
      expect(rows[line - 1].querySelector("td")?.textContent).toBe("24");
      expect(rows[rows.length - 1].textContent).toContain("University of Kentucky");
      expect(rows[rows.length - 1].querySelector("td")?.textContent).toBe("–");
    });

    it("says the first official ranking is still to come in early November", () => {
      render(<RankingsPage report={{ ...now, games_through: "2026-11-03" }} />);
      expect(screen.getByText(/hasn't published a computer ranking for this season yet/)).toBeTruthy();
    });

    it("says in the banner what \"rebuilt\" means", () => {
      render(<RankingsPage report={now} />);
      expect(screen.getByText(/"Rebuilt" means our own recalculation of the ACHA's computer ranking/)).toBeTruthy();
    });
  });
});
