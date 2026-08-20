"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/components/ui/cn";
import { StatusDot } from "@/components/ui/dot";
import { SkeletonRows } from "@/components/ui/skeleton";
import type { CatalogEntry } from "@/lib/api/types";
import { titleCase } from "./labels";

/** De-duped skill + technology tags for the details panel. */
function tagsFor(c: CatalogEntry): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of [...c.skills, ...c.technologies]) {
    const key = t.toLowerCase();
    if (!t || seen.has(key)) continue;
    seen.add(key);
    out.push(t);
  }
  return out;
}

/**
 * Single-select mission picker: a scrollable list of missions on the left,
 * a details panel for the selected one on the right. Matches the 1:1:1 run
 * contract (one mission per run) while showing the operator what they're
 * about to run — name, summary, classification, skills.
 */
export function MissionPicker({
  missions,
  selectedId,
  onSelect,
  isLoading = false,
  stacked = false,
}: {
  missions: CatalogEntry[];
  selectedId: string | null;
  onSelect: (missionId: string) => void;
  isLoading?: boolean;
  /** Layout mode. `false` (default) = the compact side-by-side used in the height-constrained
   *  Start-run dialog (details capped + scrollable). `true` = the New Run page: the details sit
   *  BELOW the list at the column's full width and grow to fit, so a long description reads in
   *  full with no cramped inner scroll. The list keeps its own scroll either way. */
  stacked?: boolean;
}) {
  const selected = missions.find((c) => c.mission_id === selectedId) ?? null;

  // While the catalog fetch is in-flight, show a loading indicator rather than the empty state
  // — an in-flight fetch would otherwise read as "no missions available".
  if (missions.length === 0 && isLoading)
    return (
      <div role="status" aria-label="Loading missions…">
        <SkeletonRows count={5} />
      </div>
    );

  if (missions.length === 0)
    return (
      <p className="text-dense text-text-tertiary">
        No missions available — ingest a bundle or connect the remote catalog.
      </p>
    );

  return (
    <div className={cn(stacked ? "space-y-3" : "grid gap-3 sm:grid-cols-2")}>
      {/* List */}
      <ul className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-border bg-background p-1">
        {missions.map((c) => {
          const active = c.mission_id === selectedId;
          return (
            <li key={c.mission_id}>
              <button
                type="button"
                aria-label={c.name}
                onClick={() => onSelect(c.mission_id)}
                data-active={active}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-dense transition-colors",
                  active ? "bg-card text-heading" : "text-text-secondary hover:bg-card",
                )}
              >
                <StatusDot tone={c.installed ? "ok" : "primary"} />
                <span className="truncate">{c.name}</span>
                {!c.installed && (
                  <span className="ml-auto shrink-0 text-label uppercase text-text-tertiary">
                    library
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>

      {/* Details. In the dialog (side-by-side) it's capped to the list height so a long
          description scrolls within instead of growing the dialog. On the New Run page (stacked)
          it grows to fit below the list — no cramped inner scroll. */}
      <div className={cn(stacked ? "" : "max-h-56 overflow-y-auto", "rounded-lg border border-border bg-background p-3")}>
        {selected ? (
          <div className="space-y-2">
            <p className="text-body font-bold text-heading">
              {selected.name}
            </p>
            {selected.summary && (
              <p className="text-dense text-text-secondary">{selected.summary}</p>
            )}
            <div className="flex flex-wrap items-center gap-1">
              <Badge variant={selected.source === "your_own" ? "ok" : "info"}>
                {selected.source === "your_own" ? "Your own" : "Library"}
              </Badge>
              {selected.specialty && (
                <Badge variant="info">{titleCase(selected.specialty)}</Badge>
              )}
              {selected.proficiency && (
                <Badge variant="muted">{titleCase(selected.proficiency)}</Badge>
              )}
              {selected.type && (
                <Badge variant="muted">{selected.type}</Badge>
              )}
              <Badge variant={selected.installed ? "ok" : "default"}>
                {selected.installed ? "installed" : "pulls on start"}
              </Badge>
            </div>
            {/* prose-tight is the declared measure for help text sitting under a
                control (68ch, 1.6) — the arbitrary max-w-[68ch] restated it by hand. */}
            {!selected.installed && (
              <p className="prose-tight text-dense text-text-tertiary">
                Not installed yet — this mission is pulled automatically when
                the run starts.
              </p>
            )}
            {tagsFor(selected).length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                {tagsFor(selected).map((t) => (
                  <Badge key={t} variant="muted">
                    {t}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        ) : (
          <p className="text-dense text-text-tertiary">
            Select a mission to see its details.
          </p>
        )}
      </div>
    </div>
  );
}
