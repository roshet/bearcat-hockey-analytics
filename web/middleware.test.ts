// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import middleware, { authorized, config } from "./middleware";

const SITE = "https://bearcat.example/";
const basic = (credentials: string) => `Basic ${btoa(credentials)}`;
const request = (authorization?: string, path = "") =>
  new Request(SITE + path, authorization ? { headers: { authorization } } : {});

afterEach(() => vi.unstubAllEnvs());

describe("the password gate", () => {
  it("asks for the password when none is sent", () => {
    vi.stubEnv("SITE_PASSWORD", "puck-drop");
    const response = middleware(request());
    expect(response?.status).toBe(401);
    expect(response?.headers.get("WWW-Authenticate")).toBe('Basic realm="Bearcat Hockey Analytics", charset="UTF-8"');
  });

  it("refuses a wrong password", () => {
    vi.stubEnv("SITE_PASSWORD", "puck-drop");
    expect(middleware(request(basic("coach:icing")))?.status).toBe(401);
  });

  it("lets the right password through with any username", () => {
    vi.stubEnv("SITE_PASSWORD", "puck-drop");
    expect(middleware(request(basic("coach:puck-drop")))).toBeUndefined();
    expect(middleware(request(basic(":puck-drop"), "data/index.json"))).toBeUndefined();
  });

  it("serves nothing when no password is configured", () => {
    vi.stubEnv("SITE_PASSWORD", "");
    expect(middleware(request(basic("coach:")))?.status).toBe(401);
    expect(authorized(basic("coach:anything"), undefined)).toBe(false);
  });

  it("treats a blank password as no password", () => {
    vi.stubEnv("SITE_PASSWORD", "   ");
    expect(middleware(request(basic("coach:   ")))?.status).toBe(401);
  });

  it("refuses malformed credentials without throwing", () => {
    vi.stubEnv("SITE_PASSWORD", "puck-drop");
    for (const header of ["Basic !!!", basic("nocolon"), "Bearer puck-drop", basic("coach:"), "Basic"]) {
      expect(middleware(request(header))?.status).toBe(401);
    }
  });

  it("guards every path, the data files included", () => {
    expect(config.matcher).toBe("/:path*");
  });
});
