"use client";

import type { ReactNode } from "react";
import { Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  EMPTY_FILTERS,
  hasActiveFilters,
  type MissionFilters,
  type FacetKey,
} from "./filter-missions";
import { FacetSelect } from "./facet-select";
import { titleCase, environmentLabel } from "./labels";

/**
 * Catalog filter chrome — two lines, both `shrink-0` so they stay put while the grid
 * below them scrolls.
 *
 *   line 1  search · Specialty · Proficiency · Clear filters
 *   line 2  the current selection, as removable amber chips (only when something is on)
 *
 * Both facets are multi-select and share one vocabulary: pick as many as you like, pick
 * none to see everything. Amber is the current selection only — it is never decoration.
 */
export function FilterBar({
  filters,
  onChange,
  specialties,
  proficiencies,
  types,
  specialtyCounts,
  proficiencyCounts,
  typeCounts,
  trailing,
}: {
  filters: MissionFilters;
  onChange: (f: MissionFilters) => void;
  specialties: string[];
  proficiencies: string[];
  types: string[];
  specialtyCounts?: Record<string, number>;
  proficiencyCounts?: Record<string, number>;
  typeCounts?: Record<string, number>;
  /** Optional chrome pinned to the right end of the search row (the provider tabs). */
  trailing?: ReactNode;
}) {
  const active = hasActiveFilters(filters);

  const chips: { facet: FacetKey; value: string }[] = [
    ...filters.specialties.map((value) => ({ facet: "specialties" as const, value })),
    ...filters.proficiencies.map((value) => ({
      facet: "proficiencies" as const,
      value,
    })),
    ...filters.types.map((value) => ({ facet: "types" as const, value })),
  ];

  const drop = (facet: FacetKey, value: string) =>
    onChange({ ...filters, [facet]: filters[facet].filter((v) => v !== value) });

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-text-secondary" />
          <Input
            aria-label="Search missions"
            placeholder="Search missions…"
            value={filters.query}
            onChange={(e) => onChange({ ...filters, query: e.target.value })}
            className="w-56 max-w-full pl-7"
          />
        </div>

        <FacetSelect
          label="Specialty"
          options={specialties}
          selected={filters.specialties}
          onChange={(next) => onChange({ ...filters, specialties: next })}
          format={titleCase}
          counts={specialtyCounts}
        />
        <FacetSelect
          label="Proficiency"
          options={proficiencies}
          selected={filters.proficiencies}
          onChange={(next) => onChange({ ...filters, proficiencies: next })}
          format={titleCase}
          counts={proficiencyCounts}
        />
        <FacetSelect
          label="Environment"
          options={types}
          selected={filters.types}
          onChange={(next) => onChange({ ...filters, types: next })}
          format={environmentLabel}
          counts={typeCounts}
        />

        {active && (
          <Button variant="ghost" size="sm" onClick={() => onChange(EMPTY_FILTERS)}>
            <X className="size-3.5" />
            Clear filters
          </Button>
        )}

        {trailing && <div className="ml-auto">{trailing}</div>}
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-label uppercase text-text-secondary">
            Filtering by
          </span>
          {chips.map(({ facet, value }) => (
            <button
              key={`${facet}:${value}`}
              type="button"
              onClick={() => drop(facet, value)}
              aria-label={`Remove filter ${titleCase(value)}`}
              className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-label uppercase text-primary transition-colors hover:bg-primary/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {titleCase(value)}
              <X className="size-3" aria-hidden />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
