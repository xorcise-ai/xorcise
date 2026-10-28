"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/components/ui/cn";
import { pct } from "@/lib/api/format";
import type { RunScore, ScoreSummary } from "@/lib/api/types";
import { asideLine, hatch, runGlyph, type RunKind } from "./run-glyph";
import { RunInspector } from "./run-inspector";
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
 * hatched. Every other kind reads as it does on the run strip: a timed-out run carries a red cap,
 * at the score it earned; one an operator stopped is grey; an environment failure is an empty grey
 * striped cell, never a score. A run on a version that is no longer installed sits in its own row,
 * marked as older: its checks and rubric may not be the current version's.
 *
 * Each cell opens that run's result. The whole ledger is one stop in the tab order; the arrow keys
 * move between cells, Home and End to a row's ends. Pointing at or focusing a cell opens the run's
 * inspector, and `onInspect` reports its kind so the key can light up the matching entry.
 */
export function RunLedger({
  rows,
  onInspect,
}: {
  rows: LedgerRow[];
  onInspect?: (kind: RunKind | null) => void;
}) {
  // The tab stop, by version and run rather than by position, so a refetch that adds a run or a
  // row keeps it on the same run. Null until a cell is focused: then the first row's newest run.
  const [active, setActive] = useState<{ row: string; run: string } | null>(null);
  const cells = useRef(new Map<string, HTMLAnchorElement>());
  const shownRuns = useRef(new Map<string, RunScore[]>());
  const activeRow = active ? rows.findIndex((r) => rowKey(r) === active.row) : -1;
  const activeHeld =
    activeRow >= 0 && rows[activeRow].runs.some((r) => r.run_id === active?.run);

  const focus = (row: number, col: number) => {
    const shown = shownRuns.current.get(rowKey(rows[row])) ?? [];
    if (shown.length === 0) return;
    const run = shown[Math.max(0, Math.min(shown.length - 1, col))];
    setActive({ row: rowKey(rows[row]), run: run.run_id });
    cells.current.get(run.run_id)?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const row = activeHeld ? activeRow : 0;
    const shown = shownRuns.current.get(rowKey(rows[row])) ?? [];
    const at = activeHeld ? shown.findIndex((r) => r.run_id === active?.run) : -1;
    const col = at < 0 ? shown.length - 1 : at;
    const moves: Record<string, () => void> = {
      ArrowRight: () => focus(row, col + 1),
      ArrowLeft: () => focus(row, col - 1),
      ArrowDown: () => row + 1 < rows.length && focus(row + 1, col),
      ArrowUp: () => row > 0 && focus(row - 1, col),
      Home: () => focus(row, 0),
      End: () => focus(row, shown.length - 1),
    };
    const move = moves[e.key];
    if (move) {
      e.preventDefault();
      move();
    }
  };

  return (
    <div role="group" aria-label="Runs by mission" onKeyDown={onKeyDown} className="space-y-2">
      {rows.map((row, i) => {
        const k = rowKey(row);
        return (
          <LedgerRowView
            key={k}
            row={row}
            stopRun={activeHeld ? (active?.row === k ? active.run : null) : i === 0 ? "newest" : null}
            onShown={(shown) => {
              shownRuns.current.set(k, shown);
            }}
            register={(runId, el) => {
              if (el) cells.current.set(runId, el);
              else cells.current.delete(runId);
            }}
            onFocusCell={(runId) => setActive({ row: k, run: runId })}
            onInspect={onInspect}
          />
        );
      })}
    </div>
  );
}

const rowKey = (row: LedgerRow) => `${row.summary.mission}@${row.summary.mission_version}`;

function LedgerRowView({
  row,
  stopRun,
  onShown,
  register,
  onFocusCell,
  onInspect,
}: {
  row: LedgerRow;
  /** The run that holds the ledger's tab stop when it is in this row, "newest" for the row's
   *  newest shown run, null when the stop is in another row. */
  stopRun: string | "newest" | null;
  onShown: (shown: RunScore[]) => void;
  register: (runId: string, el: HTMLAnchorElement | null) => void;
  onFocusCell: (runId: string) => void;
  onInspect?: (kind: RunKind | null) => void;
}) {
  const [inspected, setInspected] = useState<number | null>(null);
  const [ref, fit] = useFitCount<HTMLDivElement>(MIN_CELL, GAP, MAX_CELLS, FALLBACK_CELLS);
  const shown = row.runs.slice(-fit);
  const earlier = row.runs.length - shown.length;
  onShown(shown);
  const inspect = (col: number | null) => {
    setInspected(col);
    onInspect?.(col === null ? null : runGlyph(shown[col]).kind);
  };
  const s = row.summary;
  const older = s.current_version === false;
  const aside = asideLine(s);
  // The one tab stop: the cell last focused, else the first row's newest run. A focused run that
  // no longer fits hands the stop to its row's newest.
  const held = stopRun === null ? -1 : shown.findIndex((r) => r.run_id === stopRun);
  const stop = stopRun === null ? -1 : held >= 0 ? held : shown.length - 1;

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
      <div ref={ref} className="relative flex min-w-0 items-center" style={{ gap: GAP }}>
        {earlier > 0 && (
          <span className="sr-only">{earlier} earlier runs not shown.</span>
        )}
        {shown.map((r, col) => (
          <LedgerCell
            key={r.run_id}
            run={r}
            tabbable={col === stop}
            register={(el) => register(r.run_id, el)}
            onFocus={() => {
              onFocusCell(r.run_id);
              inspect(col);
            }}
            onInspect={(on) => inspect(on ? col : null)}
          />
        ))}
        {inspected !== null && shown[inspected] && (
          <RunInspector
            run={shown[inspected]}
            className={cn(
              "absolute top-full mt-2",
              inspected >= shown.length / 2 ? "right-0" : "left-0",
            )}
          />
        )}
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
  onInspect,
}: {
  run: RunScore;
  tabbable: boolean;
  register: (el: HTMLAnchorElement | null) => void;
  onFocus: () => void;
  onInspect: (on: boolean) => void;
}) {
  const g = runGlyph(run);
  return (
    <Link
      ref={register}
      href={`/runs/result?id=${encodeURIComponent(run.run_id)}`}
      tabIndex={tabbable ? 0 : -1}
      onFocus={onFocus}
      onBlur={() => onInspect(false)}
      onMouseEnter={() => onInspect(true)}
      onMouseLeave={() => onInspect(false)}
      aria-label={g.label}
      data-testid="ledger-cell"
      data-bucket={g.bucket}
      data-kind={g.kind}
      className={cn(
        "aspect-square min-w-0 max-w-5 flex-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        cellLook(run),
      )}
    >
      <CellFace run={run} />
    </Link>
  );
}

/** A cell's frame for its run, in the run strip's language: grey when an operator stopped it,
 *  an empty grey striped cell for an environment failure (never red: the failure is ours), an
 *  empty dashed one when grading failed, dashed and pulsing while grading. Shared with the key's examples. */
export function cellLook(run: RunScore): string {
  const g = runGlyph(run);
  return cn(
    "relative flex flex-col overflow-hidden rounded-sm",
    g.grey && "opacity-60 grayscale",
    (g.kind === "infra_failed" || g.kind === "grading_failed") &&
      "border border-dashed border-text-tertiary",
    g.kind === "grading" && "border border-dashed border-border-hover motion-safe:animate-pulse",
  );
}

/** A cell's two halves: the judge's shade above, the checks' below. */
export function CellFace({ run }: { run: RunScore }) {
  const g = runGlyph(run);
  const partial = g.judgeRange > 0;
  if (g.kind === "infra_failed")
    return (
      <span
        aria-hidden
        data-part="infra"
        className="size-full"
        style={{ background: hatch("color-mix(in srgb, var(--color-heading) 10%, transparent)") }}
      />
    );
  return (
    <>
      {/* A timed-out run's red cap, as on its strip column: it did not finish. */}
      {g.cap && <span aria-hidden data-part="cap" className="absolute inset-x-0 top-0 z-10 h-[3px] bg-err" />}
      {!g.empty && (
        <>
          <span
            aria-hidden
            data-part={g.unmeasured ? "unmeasured" : partial ? "judge-range" : "judge"}
            className={cn(
              "flex-1",
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
            className="flex-1"
            style={{ background: mix("--color-data", run.deterministic ?? 0) }}
          />
        </>
      )}
    </>
  );
}
