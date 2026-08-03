import type { CatalogEntry } from "@/lib/api/types";

export interface MissionFilters {
  query: string;
  /** Selected specialties. Empty = no narrowing (i.e. "all"). */
  specialties: string[];
  /** Selected proficiencies. Empty = no narrowing (i.e. "all"). */
  proficiencies: string[];
  /** Selected environments (metadata.type: lab | static). Empty = no narrowing. */
  types: string[];
}

export const EMPTY_FILTERS: MissionFilters = {
  query: "",
  specialties: [],
  proficiencies: [],
  types: [],
};

/** The multi-select facets, so the bar and the filter agree on one vocabulary. */
export type FacetKey = "specialties" | "proficiencies" | "types";

/**
 * Pure client-side narrowing of the catalog by search text + facets.
 *
 * Faceted-search semantics: OR *within* a facet (Web **or** Pwn), AND *across* facets
 * (Web/Pwn **and** Beginner). An empty facet never narrows — that is what "select none
 * means all" means, and it is why there is no explicit "All" option to get wrong.
 */
export function filterMissions(
  items: CatalogEntry[],
  f: MissionFilters,
): CatalogEntry[] {
  const q = f.query.trim().toLowerCase();
  return items.filter((c) => {
    if (q) {
      const hay = `${c.name} ${c.summary} ${c.mission_id}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (f.specialties.length && !f.specialties.includes(c.specialty ?? ""))
      return false;
    if (f.proficiencies.length && !f.proficiencies.includes(c.proficiency ?? ""))
      return false;
    if (f.types.length && !f.types.includes(c.type ?? "")) return false;
    return true;
  });
}

/** True when any filter would narrow the catalog (drives the "Clear filters" affordance). */
export function hasActiveFilters(f: MissionFilters): boolean {
  return Boolean(
    f.query.trim() ||
      f.specialties.length ||
      f.proficiencies.length ||
      f.types.length,
  );
}

/** Add/remove one value from a facet selection, preserving the rest (order-stable). */
export function toggleFacetValue(selected: string[], value: string): string[] {
  return selected.includes(value)
    ? selected.filter((v) => v !== value)
    : [...selected, value];
}

/**
 * Rank for proficiency tokens so the facet lists easiest → hardest rather than
 * alphabetically ("Advanced, Beginner, Expert, Hard, Intermediate" reads as noise).
 * Anything unrecognised sorts after the known ladder, alphabetically among itself.
 */
const PROFICIENCY_RANK: Record<string, number> = {
  // XORCISE ladder (Novice → Advance Beginner → Competent → Proficient → Expert)
  novice: 0,
  "advance beginner": 1,
  "advanced beginner": 1,
  competent: 2,
  proficient: 3,
  expert: 4,
  // legacy tokens, ranked onto the nearest rung
  beginner: 0,
  easy: 0,
  intro: 0,
  basic: 0,
  foundation: 0,
  medium: 1,
  intermediate: 2,
  moderate: 2,
  advanced: 3,
  hard: 3,
  elite: 4,
  insane: 4,
};

/** Distinct, ordered, non-null values of a facet across the catalog. */
export function facetValues(
  items: CatalogEntry[],
  key: "specialty" | "proficiency" | "type",
): string[] {
  const set = new Set<string>();
  for (const c of items) {
    const v = c[key];
    if (v) set.add(v);
  }
  const values = [...set];
  if (key === "proficiency") {
    const rank = (v: string) => PROFICIENCY_RANK[v.trim().toLowerCase()] ?? 99;
    return values.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  }
  return values.sort();
}

/** How many catalog entries carry each value of a facet — so the filter can show a
 *  per-option count ("Web Exploitation (2)"). Counts the full catalog, not the current
 *  narrowing, so a count reads as "how many exist" rather than shifting as you filter. */
export function facetCounts(
  items: CatalogEntry[],
  key: "specialty" | "proficiency" | "type",
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const c of items) {
    const v = c[key];
    if (v) counts[v] = (counts[v] ?? 0) + 1;
  }
  return counts;
}
