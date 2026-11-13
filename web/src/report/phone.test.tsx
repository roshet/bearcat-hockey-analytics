import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { TeamDetailReport } from "../types/detail";
import type { RankingsReport } from "../types/rankings";
import type { ScoutingReport } from "../types/report";
import { RankingsPage } from "./RankingsPage";
import { ReportPage } from "./ReportPage";
import { SpecialTeamsPage } from "./SpecialTeamsPage";
import { recorded, withRecordedData } from "../testData";

const kentuckyDetail = recorded<unknown>("kentucky-detail");
const kentucky = recorded<unknown>("kentucky");
const r73 = recorded<unknown>("rankings-73");

// The sheets are laid out for US Letter, and a coach will likely open the link on a phone
// first. Below Tailwind's `sm` (640px) the layout is phone-first; from `sm` up, which
// includes the printed page (~7.6in wide), it is the Letter layout, unchanged.
const pages = {
  scouting: () => <ReportPage report={kentucky as unknown as ScoutingReport} />,
  "special teams": () => <SpecialTeamsPage report={kentuckyDetail as unknown as TeamDetailReport} />,
  rankings: () => <RankingsPage report={r73 as unknown as RankingsReport} />,
};

afterEach(cleanup);

const classes = (el: Element) => (el.getAttribute("class") ?? "").split(/\s+/);

withRecordedData(() => {
  describe.each(Object.entries(pages))("the %s page on a phone", (_name, page) => {
    it("has at most three columns, and never sections side by side", () => {
      const { container } = render(page());
      for (const el of container.querySelectorAll("[class*='grid-cols-']")) {
        const phone = classes(el).find((c) => /^grid-cols-\d+$/.test(c));
        const cols = phone ? Number(phone.split("-")[2]) : 1;
        expect(cols, el.outerHTML.slice(0, 120)).toBeLessThanOrEqual(3);
        if (el.querySelector(":scope > section, :scope > table")) expect(cols).toBe(1);
      }
    });

    it("keeps the Letter layout from sm up", () => {
      const { container } = render(page());
      for (const el of container.querySelectorAll("[class*='grid-cols-1']")) {
        expect(classes(el).some((c) => /^sm:grid-cols-\d+$/.test(c)), el.outerHTML.slice(0, 120)).toBe(true);
      }
    });

    it("lets the header and footer wrap", () => {
      const { container } = render(page());
      const header = container.querySelector("header")!;
      expect(classes(header)).toEqual(expect.arrayContaining(["flex-col", "sm:flex-row"]));
      for (const footer of container.querySelectorAll("footer")) {
        for (const span of footer.querySelectorAll("span")) {
          expect(classes(span)).not.toContain("whitespace-nowrap");
        }
      }
    });

    it("scrolls a wide table inside its own box, not the page", () => {
      const { container } = render(page());
      for (const table of container.querySelectorAll("table.data")) {
        expect(table.closest(".overflow-x-auto"), table.outerHTML.slice(0, 120)).not.toBeNull();
      }
    });
  });
});
