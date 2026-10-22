"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/components/ui/cn";
import { pct } from "@/lib/api/format";
import type { RunScore, ScoreSummary } from "@/lib/api/types";
import { asideLine, hatch, runGlyph } from "./run-glyph";
import { useFitCount } from "./use-fit-count";

// Cell sizing. 14px keeps both halves of a cell readable as separate shades and is a workable
// pointer target in a dense grid; 60 cells caps a very wide row. Without a measurement (first
// paint, jsdom) 32 fit a ~560px row.
const MIN_CELL = 14;
const GAP = 3;
const MAX_CELLS = 60;
const FALLBACK_CELLS = 32;

const mix = (color: string, amount: number) =>
  `color-mix(in srgb, var(${color}) ${Math.round(15 + 85 * amount)}%, transparent)`;

/** One row of the ledger: an agent's runs on one mission version. */
export interface LedgerRow {
  summary: ScoreSummary;
  name: string;
  runs: RunScore[];
}

/**
 * Pair each mission-version summary with its runs: missions most recently run first, and within a
 * mission its current version first, then older versions newest first, so a mission's rows stay
 * together.
 */
export function ledgerRows(
  versions: ScoreSummary[],
  runs: RunScore[],
  missionName: (slug: string) => string,
): LedgerRow[] {
  const key = (m: string | null | undefined, v: string | null | undefined) => `${m ?? ""}@${v ?? ""}`;
  const byVersion = new Map<string, RunScore[]>();
  for (const r of runs) {
    const k = key(r.mission, r.mission_version);
    const list = byVersion.get(k);
    if (list) list.push(r);
    else byVersion.set(k, [r]);
  }
  const latest = new Map<string, string>();
  for (const s of versions) {
    const prev = latest.get(s.mission ?? "");
    if (!prev || (s.last_run ?? "") > prev) latest.set(s.mission ?? "", s.last_run ?? "");
  }
  return [...versions]
    .sort(
      (a, b) =>
        (latest.get(b.mission ?? "") ?? "").localeCompare(latest.get(a.mission ?? "") ?? "") ||
        (a.mission ?? "").localeCompare(b.mission ?? "") ||
        Number(b.current_version) - Number(a.current_version) ||
        (b.last_run ?? "").localeCompare(a.last_run ?? ""),
    )
    .map((s) => ({
      summary: s,
      name: missionName(s.mission ?? ""),
      runs: byVersion.get(key(s.mission, s.mission_version)) ?? [],
    }));
}

/**
 * An agent's runs, one row per mission VERSION, one cell per run, oldest to newest. The lower half
 * of a cell is the deterministic score and the upper half the judge's, brighter for higher. A
 * judge that never ran leaves its half as a dashed outline; one that scored part of its rubric is
 * hatched. A run that timed out or was stopped is ringed and faded. An environment failure is a
 * red ×, never a score. A run on a version that is no longer installed sits in its own row,
 * marked as older: its checks and rubric may not be the current version's.
 *
 * Each cell opens that run's result. The whole ledger is one stop in the tab order; the arrow keys
 * move between cells, Home and End to a row's ends.
 */
export function RunLedger({ rows }: { rows: LedgerRow[] }) {
  const [active, setActive] = useState<{ row: number; col: number }>({ row: 0, col: -1 });
  const cells = useRef(new Map<string, HTMLAnchorElement>());
  const shownCounts = useRef<number[]>([]);

  const focus = (row: number, col: number) => {
    const count = shownCounts.current[row] ?? 0;
    if (count === 0) return;
    const c = Math.max(0, Math.min(count - 1, col));
    setActive({ row, col: c });
    cells.current.get(`${row}:${c}`)?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const { row } = active;
    const count = shownCounts.current[row] ?? 0;
    const col = active.col < 0 ? count - 1 : active.col;
    const moves: Record<string, () => void> = {
      ArrowRight: () => focus(row, col + 1),
      ArrowLeft: () => focus(row, col - 1),
      ArrowDown: () => row + 1 < rows.length && focus(row + 1, col),
      ArrowUp: () => row > 0 && focus(row - 1, col),
      Home: () => focus(row, 0),
      End: () => focus(row, count - 1),
    };
    const move = moves[e.key];
    if (move) {
      e.preventDefault();
      move();
    }
  };

  return (
    <div role="group" aria-label="Runs by mission" onKeyDown={onKeyDown} className="space-y-2">
      {rows.map((row, i) => (
        <LedgerRowView
          key={`${row.summary.mission}@${row.summary.mission_version}`}
          row={row}
          index={i}
          active={active}
          onShown={(n) => {
            shownCounts.current[i] = n;
          }}
          register={(col, el) => {
            if (el) cells.current.set(`${i}:${col}`, el);
            else cells.current.delete(`${i}:${col}`);
          }}
          onFocusCell={(col) => setActive({ row: i, col })}
        />
      ))}
    </div>
  );
}

function LedgerRowView({
  row,
  index,
  active,
  onShown,
  register,
  onFocusCell,
}: {
  row: LedgerRow;
  index: number;
  active: { row: number; col: number };
  onShown: (n: number) => void;
  register: (col: number, el: HTMLAnchorElement | null) => void;
  onFocusCell: (col: number) => void;
}) {
  const [ref, fit] = useFitCount<HTMLDivElement>(MIN_CELL, GAP, MAX_CELLS, FALLBACK_CELLS);
  const shown = row.runs.slice(-fit);
  const earlier = row.runs.length - shown.length;
  onShown(shown.length);
  const s = row.summary;
  const older = s.current_version === false;
  const aside = asideLine(s);
  // The one tab stop: the cell last focused, else the first row's newest run.
  const stop =
    active.col < 0
      ? index === 0
        ? shown.length - 1
        : -1
      : active.row === index
        ? Math.min(active.col, shown.length - 1)
        : -1;

  return (
    <div
      role="group"
      aria-label={`${row.name} v${s.mission_version}${older ? ", older version" : ""}`}
      data-testid="ledger-row"
      className="grid grid-cols-1 items-center gap-x-4 gap-y-1 md:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_minmax(0,11rem)]"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="truncate text-dense font-bold text-heading" title={row.name}>
          {row.name}
        </span>
        <Badge variant="muted">v{s.mission_version}</Badge>
        {older && <Badge variant="warn">older</Badge>}
      </div>
      <div ref={ref} className="flex min-w-0 items-center" style={{ gap: GAP }}>
        {earlier > 0 && (
          <span className="sr-only">{earlier} earlier runs not shown.</span>
        )}
        {shown.map((r, col) => (
          <LedgerCell
            key={r.run_id}
            run={r}
            tabbable={col === stop}
            register={(el) => register(col, el)}
            onFocus={() => onFocusCell(col)}
          />
        ))}
      </div>
      <div className="flex items-baseline gap-2 text-caption md:justify-end">
        {earlier > 0 && <span className="text-text-tertiary">+{earlier} earlier ·</span>}
        <span className="text-text-tertiary">median</span>
        <span className="text-row font-bold tabular-nums text-primary">{pct(s.median)}</span>
        <span className="text-text-tertiary">
          {s.scored} of {s.runs}
        </span>
      </div>
      {aside && (
        <p className="text-caption text-text-tertiary md:col-start-2">{aside}: not in the median.</p>
      )}
    </div>
  );
}

function LedgerCell({
  run,
  tabbable,
  register,
  onFocus,
}: {
  run: RunScore;
  tabbable: boolean;
  register: (el: HTMLAnchorElement | null) => void;
  onFocus: () => void;
}) {
  const g = runGlyph(run);
  return (
    <Link
      ref={register}
      href={`/runs/result?id=${encodeURIComponent(run.run_id)}`}
      tabIndex={tabbable ? 0 : -1}
      onFocus={onFocus}
      aria-label={g.label}
      title={g.label}
      data-testid="ledger-cell"
      data-bucket={g.bucket}
      className={cn(
        "aspect-square min-w-0 max-w-5 flex-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        cellLook(run),
      )}
    >
      <CellFace run={run} />
    </Link>
  );
}

/** A cell's frame for its run: faded and ringed when it ended early, a red box for an
 *  environment failure, dashed while grading. Shared with the key's examples. */
export function cellLook(run: RunScore): string {
  const g = runGlyph(run);
  return cn(
    "relative flex flex-col rounded-[2px]",
    g.faded && "opacity-55 outline outline-[1.5px] -outline-offset-[1.5px] outline-primary",
    g.bucket === "infra_failed" &&
      "items-center justify-center border border-err text-caption leading-none text-err",
    g.bucket === "grading" && "border border-dashed border-border-hover motion-safe:animate-pulse",
  );
}

/** A cell's two halves: the judge's shade above, the checks' below. */
export function CellFace({ run }: { run: RunScore }) {
  const g = runGlyph(run);
  const partial = g.judgeRange > 0;
  return (
    <>
      {g.bucket === "infra_failed" && <span aria-hidden>×</span>}
      {!g.empty && (
        <>
          <span
            aria-hidden
            data-part={g.unmeasured ? "unmeasured" : partial ? "judge-range" : "judge"}
            className={cn(
              "flex-1 rounded-t-[2px]",
              g.unmeasured > 0 && "border border-b-0 border-dashed border-text-tertiary",
            )}
            style={
              g.unmeasured > 0
                ? undefined
                : {
                    background: partial
                      ? hatch(mix("--color-chart-4", run.judge ?? 0))
                      : mix("--color-chart-4", run.judge ?? 0),
                  }
            }
          />
          <span
            aria-hidden
            className="flex-1 rounded-b-[2px]"
            style={{ background: mix("--color-data", run.deterministic ?? 0) }}
          />
        </>
      )}
    </>
  );
}
