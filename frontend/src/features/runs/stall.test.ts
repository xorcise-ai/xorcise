import { describe, it, expect } from "vitest";
import { secondsSince, isStalled } from "./stall";

describe("stall detection", () => {
  it("secondsSince floors to whole seconds and never goes negative", () => {
    expect(secondsSince(1_000, 3_500)).toBe(2);
    // A seed clamped to now (or plain clock skew) must read 0, not a negative age.
    expect(secondsSince(5_000, 3_000)).toBe(0);
  });

  it("secondsSince is null with no mark to measure from", () => {
    expect(secondsSince(null, 1_000)).toBeNull();
    expect(secondsSince(Number.NaN, 1_000)).toBeNull();
  });

  it("isStalled trips exactly at the threshold and never on null", () => {
    expect(isStalled(179, 180)).toBe(false);
    expect(isStalled(180, 180)).toBe(true);
    expect(isStalled(null, 180)).toBe(false);
  });
});
