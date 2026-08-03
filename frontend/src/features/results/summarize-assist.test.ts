import { describe, expect, it } from "vitest";
import {
  ASSIST_BUCKET_COUNT,
  assistBucket,
  assistMixByMission,
} from "./summarize-assist";

describe("assistBucket", () => {
  it("maps 0 (and bad values) to Unassisted", () => {
    expect(assistBucket(0)).toBe(0);
    expect(assistBucket(-1)).toBe(0);
    expect(assistBucket(Number.NaN)).toBe(0);
    expect(assistBucket(Number.POSITIVE_INFINITY)).toBe(0);
  });

  it("maps 1 and 2 to their own buckets", () => {
    expect(assistBucket(1)).toBe(1);
    expect(assistBucket(2)).toBe(2);
  });

  it("collapses 3+ into the final bucket", () => {
    expect(assistBucket(3)).toBe(ASSIST_BUCKET_COUNT - 1);
    expect(assistBucket(9)).toBe(ASSIST_BUCKET_COUNT - 1);
  });
});

describe("assistMixByMission", () => {
  it("splits scored runs into unassisted vs assisted per mission", () => {
    const mix = assistMixByMission([
      { mission: "alpha", intel: 0, overall: 0.8, partial: false },
      { mission: "alpha", intel: 2, overall: 0.6, partial: false },
      { mission: "alpha", intel: 0, overall: 0.5, partial: false },
      { mission: "beta", intel: 1, overall: 0.9, partial: false },
    ]);
    expect(mix.get("alpha")).toEqual({ unassisted: 2, assisted: 1 });
    expect(mix.get("beta")).toEqual({ unassisted: 0, assisted: 1 });
  });

  it("excludes partial and ungraded runs", () => {
    const mix = assistMixByMission([
      { mission: "alpha", intel: 0, overall: null, partial: false },
      { mission: "alpha", intel: 1, overall: 0.7, partial: true },
      { mission: "alpha", intel: 0, overall: 0.4, partial: false },
    ]);
    expect(mix.get("alpha")).toEqual({ unassisted: 1, assisted: 0 });
  });
});
