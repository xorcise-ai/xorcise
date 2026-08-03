import { describe, it, expect } from "vitest";
import {
  titleCase,
  environmentLabel,
  isLab,
  difficultyVariant,
  proficiencyLevel,
  describeCheck,
} from "./labels";

describe("titleCase", () => {
  it("normalises hyphen/underscore tokens", () => {
    expect(titleCase("lateral-movement")).toBe("Lateral Movement");
    expect(titleCase("web_service")).toBe("Web Service");
    expect(titleCase("network")).toBe("Network");
  });
});

describe("environmentLabel", () => {
  it("maps the real lab/static values", () => {
    expect(environmentLabel("lab")).toBe("Lab");
    expect(environmentLabel("static")).toBe("Static");
    expect(environmentLabel("LAB")).toBe("Lab");
  });
  it("title-cases a legacy value", () => {
    expect(environmentLabel("ctf")).toBe("Ctf");
  });
});

describe("isLab", () => {
  it("is true only for lab", () => {
    expect(isLab("lab")).toBe(true);
    expect(isLab("static")).toBe(false);
    expect(isLab(null)).toBe(false);
    expect(isLab(undefined)).toBe(false);
  });
});

describe("proficiencyLevel", () => {
  it("maps the XORCISE ladder Novice→Expert to tiers 1–5", () => {
    expect(proficiencyLevel("Novice")).toBe(1);
    expect(proficiencyLevel("Advance Beginner")).toBe(2);
    expect(proficiencyLevel("Competent")).toBe(3);
    expect(proficiencyLevel("Proficient")).toBe(4);
    expect(proficiencyLevel("Expert")).toBe(5);
    expect(proficiencyLevel("mystery")).toBeNull();
  });
});

describe("difficultyVariant", () => {
  it("maps the proficiency ladder to colour variants", () => {
    expect(difficultyVariant("Novice")).toBe("ok");
    expect(difficultyVariant("Advance Beginner")).toBe("ok");
    expect(difficultyVariant("Competent")).toBe("default");
    expect(difficultyVariant("Proficient")).toBe("err");
    expect(difficultyVariant("Expert")).toBe("err");
    expect(difficultyVariant("mystery")).toBe("muted");
  });
});

describe("describeCheck", () => {
  it("splits a raw check into title / rule / source", () => {
    expect(
      describeCheck({
        id: "flag-correct",
        op: "equals",
        ref: "flag",
        source: "artifacts",
      }),
    ).toEqual({ title: "Flag Correct", rule: "Equals flag", source: "Artifacts" });
  });
  it("keeps an unknown op/source readable", () => {
    const d = describeCheck({ id: "x", op: "startswith", ref: "y", source: "otel-stats" });
    expect(d.rule).toBe("Startswith y");
    expect(d.source).toBe("OTel stats");
  });
});
