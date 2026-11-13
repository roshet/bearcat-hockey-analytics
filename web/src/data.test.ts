import { describe, expect, it } from "vitest";
import { dataUrl, freshnessText } from "./data";

describe("dataUrl", () => {
  it("points each view at the exported file", () => {
    expect(dataUrl("teams", 73)).toBe("/data/73/teams.json");
    expect(dataUrl("scouting", 73, 346)).toBe("/data/73/scouting/346.json");
    expect(dataUrl("special-teams", 60, 265)).toBe("/data/60/special-teams/265.json");
    expect(dataUrl("rankings", 73)).toBe("/data/73/rankings.json");
  });
});

describe("freshnessText", () => {
  const index = { current_season: 73, seasons: [73], built_at: "2026-10-24T11:00:00+00:00" };
  it("says how current the data is", () => {
    expect(freshnessText({ ...index, data_through: "2026-10-22" })).toBe("Data through Oct 22 · updated Oct 24");
  });
  it("leaves out the data date when there is none", () => {
    expect(freshnessText({ ...index, data_through: null })).toBe("updated Oct 24");
  });
});
