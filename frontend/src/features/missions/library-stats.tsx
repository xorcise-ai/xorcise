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
      {/* An explicit grid, not flex-wrap. Wrapping let the row decide for itself how many
          cells fit, and on a ~500px pane that came out 3-up with difficulty dropped alone
          onto a second row — the widest cell, given the whole width, with nothing beside
          it. Four cells split cleanly, so the layout is stated: one column on a phone, 2x2
          once there is room for two, one row at lg where all four fit without squeezing
          the difficulty meter (it needs ~210px and cannot compress). */}
      <CardContent className="grid grid-cols-1 items-stretch gap-x-6 gap-y-4 p-4 min-[30rem]:grid-cols-2 lg:grid-cols-4">
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
          {/* Capped, unlike its neighbours. Every other cell has something that USES spare
              width — a bar that stretches, a number that grows. A difficulty row is a
              label, five fixed dots and a count: about 210px of content, and nothing in it
              can absorb a pixel more. Uncapped, the cell still stretched with the strip,
              and when the strip wraps on a narrow pane this block takes a whole row on its
              own — which is where it was last reported, the count marooned 300px from the
              meter it belongs to. The cap is what keeps the row reading as one unit. */}
          <div className="max-w-[17rem] space-y-2">
            <p className="text-label uppercase text-text-tertiary">
              difficulty
            </p>
            {stats.byDifficulty.map((b) => (
              <div
                key={b.label}
                /* 1fr on the LABEL, not the pips: an `auto` middle track absorbs the spare
                   width and left-aligns five fixed-width dots inside it, which detaches the
                   meter from its count. Spare width belongs to the truncating label. */
                className="grid grid-cols-[1fr_auto_18px] items-center gap-2 text-caption text-text-secondary"
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
 * A strip cell. `min-w-0` so a grid cell can shrink under its content and let the labels
 * inside truncate as intended — grid items default to `min-width: auto`, which would push
 * the widest cell past its track instead.
 *
 * `divider` draws the left rule at lg and only at lg, where the four cells sit in ONE row
 * and every rule separates two cells. In the 2x2 and stacked layouts the same rule lands
 * on the cell that STARTS a row, where it reads as a stray vertical line down the left of
 * the card; the grid gap already does the separating there.
 */
function Section({ children, divider }: { children: React.ReactNode; divider?: boolean }) {
  return (
    <div className={cn("min-w-0", divider && "lg:border-l lg:border-border lg:pl-6")}>
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
