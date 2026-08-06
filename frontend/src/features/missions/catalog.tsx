"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { LayoutGrid, List, Search } from "lucide-react";
import { cn } from "@/components/ui/cn";
import { Button } from "@/components/ui/button";
import { SkeletonCardGrid } from "@/components/ui/skeleton";
import { Reveal } from "@/components/ui/reveal";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useMissions, useCatalogStatus } from "./queries";
import {
  filterMissions,
  facetValues,
  facetCounts,
  hasActiveFilters,
  EMPTY_FILTERS,
} from "./filter-missions";
import { FilterBar } from "./filter-bar";
import { MissionCard, MissionRow } from "./mission-card";
import { LibraryStats } from "./library-stats";
import { IngestButton } from "./ingest-button";
import { IngestComingSoon } from "./ingest-coming-soon";
import { OtherProviders } from "./other-providers";
import type { CatalogEntry, CatalogStatus } from "@/lib/api/types";

/** localStorage key the catalog uses to remember the grid/list view between visits. */
const VIEW_STORAGE_KEY = "xorcise:missions:view";

export function MissionCatalog() {
  const missions = useMissions();
  const status = useCatalogStatus();
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  // Default to "grid" on the first render (deterministic — no SSR/hydration mismatch), then adopt
  // the remembered choice once mounted.
  const [view, setView] = useState<MissionView>("grid");
  useEffect(() => {
    const saved = localStorage.getItem(VIEW_STORAGE_KEY);
    if (saved === "grid" || saved === "list") setView(saved);
  }, []);
  const changeView = (next: MissionView) => {
    setView(next);
    localStorage.setItem(VIEW_STORAGE_KEY, next);
  };

  const all = useMemo(() => missions.data ?? [], [missions.data]);
  const filtered = useMemo(() => filterMissions(all, filters), [all, filters]);
  const yourOwn = filtered.filter((c) => c.source === "your_own");
  const library = filtered.filter((c) => c.source === "library");
  const disconnected = status.data?.state === "disconnected";
  const filtersActive = hasActiveFilters(filters);
  const clearFilters = () => setFilters(EMPTY_FILTERS);

  // Land on the tab that actually holds missions. A brand-new install has one ingested
  // bundle and twenty-odd remote ones, and opening onto a near-empty grid hides the real
  // catalog behind a click. Ties keep Your Own (it is the user's own shelf).
  const ownedTotal = all.filter((c) => c.source === "your_own").length;
  const libraryTotal = all.length - ownedTotal;
  const defaultTab = ownedTotal >= libraryTotal ? "your_own" : "library";

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <header className="flex shrink-0 flex-wrap items-start justify-between gap-3">
        <div>
          <p className="mb-1 text-label uppercase text-text-secondary">
            Missions
          </p>
          <h1 className="text-lead text-heading">Mission Catalog</h1>
        </div>
        <div className="flex items-center gap-2">
          <ViewToggle view={view} onChange={changeView} />
          <IngestButton />
        </div>
      </header>

      {missions.isLoading && (
        <div role="status" aria-label="Loading catalog…">
          <SkeletonCardGrid count={6} />
        </div>
      )}
      {missions.isError && (
        <p className="text-body text-err">Couldn’t load the catalog.</p>
      )}

      {missions.data && (
        <Tabs defaultValue={defaultTab} className="flex min-h-0 flex-1 flex-col">
          {/* One unbounded scroller for the whole catalog: the stats dashboard scrolls up and off,
             while the filters + provider tabs row below stays pinned to the top.
             A flex COLUMN, so the tab body below can claim the leftover height instead of
             guessing it from 100vh — Other providers needs a canvas that exactly fills the
             pane, and a vh estimate overshot it by the height of this strip and scrolled. */}
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto pr-1">
            {all.length > 0 && (
              <div className="mb-3 shrink-0">
                <LibraryStats missions={all} />
              </div>
            )}

            <div className="sticky top-0 z-20 shrink-0 border-b border-border bg-background pb-2">
              <FilterBar
                filters={filters}
                onChange={setFilters}
                specialties={facetValues(all, "specialty")}
                proficiencies={facetValues(all, "proficiency")}
                types={facetValues(all, "type")}
                specialtyCounts={facetCounts(all, "specialty")}
                proficiencyCounts={facetCounts(all, "proficiency")}
                typeCounts={facetCounts(all, "type")}
                trailing={
                  <TabsList>
                    <TabsTrigger value="your_own">
                      Your Own
                      <Count n={yourOwn.length} />
                    </TabsTrigger>
                    <TabsTrigger value="library">
                      XORCISE Remote
                      <Count n={library.length} />
                      <StatusDot status={status.data} />
                    </TabsTrigger>
                    <TabsTrigger value="other" className="text-text-secondary">
                      Other providers
                    </TabsTrigger>
                  </TabsList>
                }
              />
            </div>

            {/* flex-1 with the default min-height:auto — grows to fill when a tab's content
                is short (Other providers), stays at content height and lets the scroller
                scroll when it is long (a 26-card grid). */}
            <div className="flex flex-1 flex-col pt-3">
              {/* ── Your Own (local, ingested) ── */}
              <TabsContent value="your_own" className="space-y-2">
                <p className="text-dense text-text-secondary">
                  Missions you’ve ingested locally from a bundle.
                </p>
                <Grid
                  items={yourOwn}
                  view={view}
                  filtersActive={filtersActive}
                  onClearFilters={clearFilters}
                  empty={<NoLocalMissions />}
                />
              </TabsContent>

              {/* ── XORCISE Remote (the free library) ── */}
              <TabsContent value="library" className="space-y-2">
                <RemoteStatusLine status={status.data} />
                {disconnected ? (
                  <p className="text-body text-text-secondary">
                    The remote catalog is disconnected.{" "}
                    <Link href="/settings" className="text-primary underline">
                      Connect it in Settings
                    </Link>{" "}
                    to browse and pull XORCISE missions.
                  </p>
                ) : (
                  <Grid
                    items={library}
                    view={view}
                    filtersActive={filtersActive}
                    onClearFilters={clearFilters}
                    empty={
                      <p className="text-body text-text-secondary">
                        No remote missions available yet.
                      </p>
                    }
                  />
                )}
              </TabsContent>

              {/* ── Other providers (committed, not shipped) ── */}
              <TabsContent value="other" className="flex flex-1 flex-col">
                <OtherProviders />
              </TabsContent>
            </div>
          </div>
        </Tabs>
      )}
    </div>
  );
}

type MissionView = "grid" | "list";

/** Grid ↔ list segmented toggle for the catalog. Grid is the default wide card view; list is the
 *  dense one-row-per-mission view. */
function ViewToggle({
  view,
  onChange,
}: {
  view: MissionView;
  onChange: (v: MissionView) => void;
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-md border border-border">
      <ViewToggleButton
        active={view === "grid"}
        onClick={() => onChange("grid")}
        label="Grid view"
      >
        <LayoutGrid className="size-3.5" />
      </ViewToggleButton>
      <ViewToggleButton
        active={view === "list"}
        onClick={() => onChange("list")}
        label="List view"
      >
        <List className="size-3.5" />
      </ViewToggleButton>
    </div>
  );
}

function ViewToggleButton({
  active,
  onClick,
  label,
  children,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "flex size-8 items-center justify-center border-r border-border/60 transition-colors last:border-r-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        active ? "bg-raised text-primary" : "text-text-secondary hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function Count({ n }: { n: number }) {
  return <span className="ml-1.5 text-text-secondary">({n})</span>;
}

/** Small coloured dot in the Remote tab trigger reflecting reachability. */
function StatusDot({ status }: { status?: CatalogStatus }) {
  const color =
    status?.state === "connected"
      ? "var(--color-ok)"
      : status?.state === "error"
        ? "var(--color-err)"
        : "var(--color-text-tertiary)";
  return (
    <span
      className="ml-1.5 inline-block size-1.5 rounded-full align-middle"
      style={{ backgroundColor: color }}
      aria-hidden
    />
  );
}

/** A one-line connection status above the remote grid. */
function RemoteStatusLine({ status }: { status?: CatalogStatus }) {
  const state = status?.state ?? "—";
  const tone =
    state === "connected"
      ? "text-ok"
      : state === "error"
        ? "text-err"
        : "text-text-secondary";
  return (
    <p className={`flex items-center gap-2 text-caption ${tone}`}>
      <StatusDot status={status} />
      <span className="capitalize">{state}</span>
      {status?.message && (
        <span className="text-text-secondary">· {status.message}</span>
      )}
    </p>
  );
}

function Grid({
  items,
  view,
  empty,
  filtersActive,
  onClearFilters,
}: {
  items: CatalogEntry[];
  view: MissionView;
  /** Rendered as given, not wrapped: one tab's empty state is a sentence, another's is a
   *  panel, and a shared <p> around both would have set the panel in body type. */
  empty: ReactNode;
  filtersActive: boolean;
  onClearFilters: () => void;
}) {
  if (items.length === 0) {
    // Filters first: "nothing matched your search" is a different problem from "this tab
    // has nothing in it", and only the first one has a way out.
    return filtersActive ? <NoResults onClear={onClearFilters} /> : empty;
  }
  // List view: one dense row per mission.
  if (view === "list") {
    return (
      <ul className="space-y-1">
        {items.map((c) => (
          <MissionRow key={c.mission_id} mission={c} />
        ))}
      </ul>
    );
  }
  // Grid view: capped at four across (the established catalog rhythm) so wide screens don't shrink
  // the cards into a five- or six-up band; steps down responsively on narrower panes.
  return (
    <ul className="grid auto-rows-fr gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {items.map((c, i) => (
        <li key={c.mission_id}>
          <Reveal delay={i * 40} className="h-full">
            <MissionCard mission={c} />
          </Reveal>
        </li>
      ))}
    </ul>
  );
}

/**
 * Your Own with nothing in it — which, until ingestion ships, is every install.
 *
 * It used to read "No local missions yet. Ingest a bundle to add one." That sentence
 * instructed the reader to perform the one action the build cannot do: the Ingest button
 * eight pixels above it opens a coming-soon preview, not a file browser. An empty state
 * that names an unavailable action as the remedy is worse than one that says nothing,
 * because the reader spends their time looking for the control rather than moving on.
 *
 * So it says the true thing instead, in the same words the Ingest dialog uses — the panel
 * is literally the same component — and then points at the shelf that does work today.
 * Only reached when NO filters are active; a search that matches nothing still gets
 * `NoResults`, which is a different problem with a different way out.
 */
function NoLocalMissions() {
  return (
    <div className="mx-auto flex w-full max-w-[42rem] flex-col gap-3 py-2 sm:py-6">
      <IngestComingSoon align="center" />
      {/* Outside the panel deliberately: the panel's own rule is that it renders nothing
          that looks actionable, so the "what can I do right now" line lives beside it. */}
      <p className="text-center text-caption text-text-tertiary">
        Until then, XORCISE Remote has missions ready to pull.
      </p>
    </div>
  );
}

/** Shown when filters narrow a tab to nothing — explains and offers a way out. */
function NoResults({ onClear }: { onClear: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-border bg-card/40 px-6 py-6 text-center">
      <Search className="size-5 text-text-secondary" />
      <p className="text-body font-medium text-heading">No missions match your filters</p>
      <p className="max-w-sm text-body text-text-secondary">
        Try a different search, or clear the filters to see everything.
      </p>
      <Button variant="outline" size="sm" onClick={onClear}>
        Clear filters
      </Button>
    </div>
  );
}
