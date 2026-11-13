import { describe, expect, it } from "vitest";
import { UC, landingTeam, urlFor } from "./landing";

const game = (opponent_id: number) => ({ game_id: 1, date: "2026-10-24", home: true, opponent_id, opponent: "x" });

describe("landingTeam", () => {
  it("opens on UC's next opponent", () => {
    expect(landingTeam({ next_games: [game(352), game(52)] }, [UC, 52, 352])).toBe(352);
  });

  it("skips opponents without a report (not MD1)", () => {
    expect(landingTeam({ next_games: [game(999), game(52)] }, [UC, 52])).toBe(52);
  });

  it("falls back to UC with no upcoming MD1 games", () => {
    expect(landingTeam({ next_games: [] }, [UC, 52])).toBe(UC);
    expect(landingTeam({ next_games: [game(999)] }, [UC, 52])).toBe(UC);
  });
});

describe("urlFor", () => {
  const base = "http://127.0.0.1:5173/";

  it("leaves the automatic team out, so a bookmark follows the next opponent", () => {
    expect(urlFor(base, 411, "scouting", true)).toBe(base);
    expect(urlFor(base, 411, "special-teams", true)).toBe(`${base}?view=special-teams`);
  });

  it("writes a team someone picked", () => {
    expect(urlFor(base, 346, "scouting", false)).toBe(`${base}?team=346`);
    expect(urlFor(`${base}?team=346&view=special-teams`, 346, "scouting", false)).toBe(`${base}?team=346`);
  });

  it("keeps view=rankings", () => {
    expect(urlFor(base, 411, "rankings", true)).toBe(`${base}?view=rankings`);
  });

  it("keeps an explicit season", () => {
    expect(urlFor(`${base}?season=60`, 411, "scouting", true)).toBe(`${base}?season=60`);
  });
});
