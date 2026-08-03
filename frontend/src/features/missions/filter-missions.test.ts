import { describe, it, expect } from "vitest";
import {
  filterMissions,
  facetValues,
  hasActiveFilters,
  toggleFacetValue,
  EMPTY_FILTERS,
} from "./filter-missions";
import { missionFixture } from "@/test/fixtures";

const items = [
  missionFixture({
    mission_id: "sqli-login",
    name: "SQLi Login",
    specialty: "web",
    proficiency: "beginner",
  }),
  missionFixture({
    mission_id: "buffer-overflow",
    name: "Stack Smash",
    summary: "classic pwn",
    specialty: "pwn",
    proficiency: "advanced",
  }),
  missionFixture({
    mission_id: "packet-trace",
    name: "Packet Trace",
    specialty: "forensics",
    proficiency: "beginner",
  }),
];

const ids = (list: ReturnType<typeof filterMissions>) =>
  list.map((c) => c.mission_id);

describe("filterMissions", () => {
  it("returns everything with empty filters", () => {
    expect(filterMissions(items, EMPTY_FILTERS)).toHaveLength(3);
  });

  it("matches the query against name/summary/id", () => {
    expect(ids(filterMissions(items, { ...EMPTY_FILTERS, query: "smash" }))).toEqual([
      "buffer-overflow",
    ]);
    expect(ids(filterMissions(items, { ...EMPTY_FILTERS, query: "sqli" }))).toEqual([
      "sqli-login",
    ]);
  });

  it("treats an empty facet as 'all'", () => {
    expect(
      filterMissions(items, { ...EMPTY_FILTERS, specialties: [] }),
    ).toHaveLength(3);
  });

  it("ORs within a facet — several specialties select their union", () => {
    expect(
      ids(filterMissions(items, { ...EMPTY_FILTERS, specialties: ["pwn"] })),
    ).toEqual(["buffer-overflow"]);
    expect(
      ids(
        filterMissions(items, { ...EMPTY_FILTERS, specialties: ["pwn", "web"] }),
      ),
    ).toEqual(["sqli-login", "buffer-overflow"]);
  });

  it("ANDs across facets — specialty AND proficiency both have to match", () => {
    expect(
      ids(
        filterMissions(items, {
          ...EMPTY_FILTERS,
          specialties: ["pwn", "web"],
          proficiencies: ["beginner"],
        }),
      ),
    ).toEqual(["sqli-login"]);
  });

  it("selects the union of several proficiencies", () => {
    expect(
      ids(
        filterMissions(items, {
          ...EMPTY_FILTERS,
          proficiencies: ["beginner", "advanced"],
        }),
      ),
    ).toEqual(["sqli-login", "buffer-overflow", "packet-trace"]);
  });

  it("combines the query with the facets", () => {
    expect(
      ids(
        filterMissions(items, {
          ...EMPTY_FILTERS,
          query: "packet",
          proficiencies: ["beginner"],
        }),
      ),
    ).toEqual(["packet-trace"]);
  });
});

describe("facetValues", () => {
  it("returns sorted distinct specialties", () => {
    expect(facetValues(items, "specialty")).toEqual(["forensics", "pwn", "web"]);
  });

  it("orders proficiencies easiest → hardest, not alphabetically", () => {
    const ladder = [
      missionFixture({ mission_id: "a", proficiency: "expert" }),
      missionFixture({ mission_id: "b", proficiency: "beginner" }),
      missionFixture({ mission_id: "c", proficiency: "hard" }),
      missionFixture({ mission_id: "d", proficiency: "intermediate" }),
    ];
    expect(facetValues(ladder, "proficiency")).toEqual([
      "beginner",
      "intermediate",
      "hard",
      "expert",
    ]);
  });
});

describe("toggleFacetValue", () => {
  it("adds a value it doesn't have and removes one it does", () => {
    expect(toggleFacetValue([], "web")).toEqual(["web"]);
    expect(toggleFacetValue(["web"], "pwn")).toEqual(["web", "pwn"]);
    expect(toggleFacetValue(["web", "pwn"], "web")).toEqual(["pwn"]);
  });
});

describe("hasActiveFilters", () => {
  it("is false for empty filters and true once any is set", () => {
    expect(hasActiveFilters(EMPTY_FILTERS)).toBe(false);
    expect(hasActiveFilters({ ...EMPTY_FILTERS, query: "  " })).toBe(false);
    expect(hasActiveFilters({ ...EMPTY_FILTERS, query: "x" })).toBe(true);
    expect(hasActiveFilters({ ...EMPTY_FILTERS, specialties: ["web"] })).toBe(true);
    expect(
      hasActiveFilters({ ...EMPTY_FILTERS, proficiencies: ["beginner"] }),
    ).toBe(true);
  });
});
