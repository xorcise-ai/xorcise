import { useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/components/ui/cn";
import { proficiencyLevel, titleCase } from "./labels";
import type { CatalogEntry } from "@/lib/api/types";

/** Proficiency tier (1–5) → label, in ascending order. Index = tier − 1. */
const TIER_LABELS = [
  "Novice",
  "Advance Beginner",
  "Competent",
  "Proficient",
  "Expert",
] as const;
/** How many specialties the strip lists before collapsing the tail into "+N". */
const STRIP_SPECIALTIES = 3;

interface SpecialtyBar {
  label: string;
  count: number;
}

interface DifficultyBar {
  label: string;
  count: number;
  /** 1–5, drives the pip meter (matches the card's DifficultyBadge). */
  level: number;
}

interface LibrarySummary {
  total: number;
  installed: number;
  available: number;
  bySpecialty: SpecialtyBar[];
  byDifficulty: DifficultyBar[];
}

function computeStats(items: CatalogEntry[]): LibrarySummary {
  const total = items.length;
  const installed = items.filter((c) => c.installed).length;

  const specialty = new Map<string, number>();
  const tier = [0, 0, 0, 0, 0];

  for (const c of items) {
    if (c.specialty) specialty.set(c.specialty, (specialty.get(c.specialty) ?? 0) + 1);
    const level = c.proficiency ? proficiencyLevel(c.proficiency) : null;
    if (level) tier[level - 1] += 1;
  }

  const bySpecialty = [...specialty.entries()]
    .map(([label, count]) => ({ label: titleCase(label), count }))
    .sort((a, b) => b.count - a.count);
  // All five tiers, always — the pip ladder reads as a consistent scale even where a tier is empty.
  const byDifficulty = TIER_LABELS.map((label, i) => ({
    label,
    count: tier[i],
    level: i + 1,
  }));

  return {
    total,
    installed,
    available: total - installed,
    bySpecialty,
    byDifficulty,
  };
}

/**
 * A single dense telemetry strip over the catalog: headline count, install progress, and the
 * specialty / difficulty breakdowns inline. Amber is monochrome — value is carried by bar length
 * and by struck pips (the brand's dot-matrix data language) — while the install fill is the data
 * green reserved for measured / installed state.
 */
export function LibraryStats({ missions }: { missions: CatalogEntry[] }) {
  const stats = useMemo(() => computeStats(missions), [missions]);
  if (stats.total === 0) return null;

  const shownSpecialties = stats.bySpecialty.slice(0, STRIP_SPECIALTIES);
  const extraSpecialties = stats.bySpecialty.length - shownSpecialties.length;
  const specialtyMax = Math.max(1, ...stats.bySpecialty.map((b) => b.count));
  const installPct = stats.total > 0 ? (stats.installed / stats.total) * 100 : 0;

  return (
    <Card className="bg-raised">
      <CardContent className="flex flex-wrap items-stretch gap-x-6 gap-y-4 p-4">
        {/* ── headline count — number stacked over the label ── */}
        <Section>
          <div>
            <span className="block text-4xl font-bold tabular-nums leading-none text-primary">
              {stats.total}
            </span>
            <p className="mt-2 text-label uppercase text-text-tertiary">
              missions
            </p>
          </div>
        </Section>

        {/* ── install progress ── */}
        <Section divider>
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-label uppercase text-text-tertiary">
                installed
              </span>
              <span className="text-dense font-semibold tabular-nums text-ok">
                {stats.installed} / {stats.total}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-primary/15">
              <div
                className="h-full rounded-full bg-ok"
                style={{ width: `${installPct}%` }}
              />
            </div>
            <p className="mt-2 text-caption text-text-tertiary">
              {stats.available} available to pull
            </p>
          </div>
        </Section>

        {/* ── specialty distribution ── */}
        <Section divider>
          <div className="space-y-2">
            <p className="text-label uppercase text-text-tertiary">
              specialty
            </p>
            {shownSpecialties.map((b) => (
              <div
                key={b.label}
                className="grid grid-cols-[64px_1fr_18px] items-center gap-2 text-caption text-text-secondary"
              >
                <span className="truncate" title={b.label}>
                  {b.label}
                </span>
                <div className="h-1.5 min-w-0 overflow-hidden rounded-full bg-primary/15">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${(b.count / specialtyMax) * 100}%` }}
                  />
                </div>
                <span className="text-right tabular-nums text-text-tertiary">{b.count}</span>
              </div>
            ))}
            {extraSpecialties > 0 && (
              <p className="text-caption text-text-tertiary">+{extraSpecialties} more</p>
            )}
          </div>
        </Section>

        {/* ── difficulty distribution — dot-matrix pips (matches the card badge) ── */}
        <Section divider>
          <div className="space-y-2">
            <p className="text-label uppercase text-text-tertiary">
              difficulty
            </p>
            {stats.byDifficulty.map((b) => (
              <div
                key={b.label}
                className="grid grid-cols-[104px_auto_18px] items-center gap-2 text-caption text-text-secondary"
              >
                <span className="truncate" title={b.label}>
                  {b.label}
                </span>
                <Pips level={b.level} />
                <span
                  className={cn(
                    "text-right tabular-nums",
                    b.count > 0 ? "text-text-tertiary" : "text-text-tertiary/50",
                  )}
                >
                  {b.count}
                </span>
              </div>
            ))}
          </div>
        </Section>
      </CardContent>
    </Card>
  );
}

/**
 * A strip cell. Cells share the row width evenly (`flex-1`) so the strip spreads across the pane;
 * `divider` draws the left rule that separates it from the previous cell.
 */
function Section({ children, divider }: { children: React.ReactNode; divider?: boolean }) {
  return (
    <div className={cn("min-w-40 flex-1", divider && "sm:border-l sm:border-border sm:pl-6")}>
      {children}
    </div>
  );
}

/** Five amber pips filled to `level` — the same dot-matrix meter the mission cards use. */
function Pips({ level }: { level: number }) {
  return (
    <span className="flex items-center gap-0.5" aria-hidden title={`Difficulty ${level} of 5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span
          key={i}
          className={cn("size-1.5 rounded-full", i <= level ? "bg-primary" : "bg-primary/20")}
        />
      ))}
    </span>
  );
}
