import { describe, it, expect } from "vitest";
import { pct, shortTime } from "./format";

describe("pct", () => {
  it("formats a 0..1 score as a whole-number percentage", () => {
    expect(pct(0)).toBe("0%");
    expect(pct(0.5)).toBe("50%");
    expect(pct(1)).toBe("100%");
    expect(pct(0.834)).toBe("83%");
  });

  it("returns an em dash for null/undefined", () => {
    expect(pct(null)).toBe("—");
    expect(pct(undefined)).toBe("—");
  });
});

describe("shortTime", () => {
  it("trims an ISO timestamp to minutes", () => {
    expect(shortTime("2026-06-29T10:00:00Z")).toBe("2026-06-29 10:00");
  });

  it("returns an em dash for empty input", () => {
    expect(shortTime(null)).toBe("—");
    expect(shortTime(undefined)).toBe("—");
  });
});
