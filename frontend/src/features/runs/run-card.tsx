"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Clock, Crosshair, Terminal, Timer, Trash2 } from "lucide-react";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/components/ui/cn";
import { useToastStore } from "@/stores/toasts";
import { runPresentation, isTerminal, type RunTone } from "./run-state";
import { DeleteRunInline } from "./delete-run-inline";
import { shortTime } from "@/lib/api/format";
import { useAgents } from "@/features/agents/queries";
import { HARNESSES, HarnessGlyph } from "@/features/agents/harnesses";
import type { RunEntry } from "@/lib/api/types";

/** Human harness name for a run's `source_agent`, or null when it's the generic
 *  fallback (no specific harness to surface — report §10: "Harness, when available"). */
function harnessLabel(sourceAgent: string): string | null {
  if (!sourceAgent || sourceAgent === "generic") return null;
  return HARNESSES.find((h) => h.kind === sourceAgent)?.name ?? sourceAgent;
}

/** Render a budget in seconds as a compact "Nm" / "Ns" string. */
function formatBudget(seconds: number): string {
  if (!seconds || seconds <= 0) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

/** Elapsed wall-clock of a run, "10m 02s" style (seconds zero-padded when minutes show). */
function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

/** Elapsed seconds for a run: created → completed for a finished run, else created → now
 *  (a render-time snapshot for a still-running one). Null when timestamps don't parse. */
function elapsedSeconds(run: RunEntry): number | null {
  const start = Date.parse(run.created_at);
  if (Number.isNaN(start)) return null;
  const end = run.completed_at ? Date.parse(run.completed_at) : Date.now();
  if (Number.isNaN(end)) return null;
  return (end - start) / 1000;
}

/** §10 status tone → the existing Badge variant carrying the same colour. */
const toneBadge: Record<RunTone, NonNullable<BadgeProps["variant"]>> = {
  amber: "default",
  green: "ok",
  red: "err",
  muted: "muted",
};

/** §10 status tone → accent colour for the left border + live pulse dot. */
const toneAccent: Record<RunTone, string> = {
  amber: "var(--color-primary)",
  green: "var(--color-ok)",
  red: "var(--color-err)",
  muted: "var(--color-muted-foreground)",
};

export function RunCard({ run }: { run: RunEntry }) {
  const view = runPresentation(run.state, run.terminal_trigger);
  const accent = toneAccent[view.tone];
  const active = !isTerminal(run);
  const harness = harnessLabel(run.source_agent);
  const elapsed = elapsedSeconds(run);
  // Delete is terminal-only: an active run 409s on DELETE (the live view owns Terminate).
  const deletable = !active;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const pushToast = useToastStore((s) => s.push);

  // Operators think in agent names, not opaque ids — resolve via the agents list
  // (falls back to a short id only if the agent can't be found).
  const agents = useAgents();
  const agentName =
    agents.data?.find((a) => a.id === run.agent_id)?.name ??
    run.agent_id.slice(0, 8);

  const href =
    view.action.target === "result"
      ? `/runs/result?id=${encodeURIComponent(run.run_id)}`
      : `/runs/live?id=${encodeURIComponent(run.run_id)}`;

  // Stretched-link pattern: the Link wraps the card content while the delete
  // affordance + its dialog sit OUTSIDE the anchor (siblings in the root div),
  // so interacting with them can never navigate.
  return (
    <div className="group relative h-full">
      <Link
        href={href}
        // While the inline confirmation is open the card must stop being a link: it sits
        // directly under the prompt, and `aria-hidden` keeps a screen reader from reading the
        // record the operator is being asked to destroy as if it were still actionable.
        aria-hidden={confirmDelete}
        tabIndex={confirmDelete ? -1 : undefined}
        className={cn(
          "block h-full transition-[filter,opacity] duration-150",
          confirmDelete && "pointer-events-none",
        )}
      >
        <Card
          className="flex h-full min-h-[8.5rem] flex-col p-3 transition-colors hover:border-border-hover"
          style={{ borderLeftWidth: 3, borderLeftColor: accent }}
        >
          <div
            className={cn(
              "flex items-start justify-between gap-2",
              // Reserve the corner the hover-revealed delete icon occupies.
              deletable && "pr-10",
            )}
          >
            <div className="flex min-w-0 items-center gap-2">
              {active && (
                <span
                  className="size-2 shrink-0 motion-safe:animate-pulse rounded-full"
                  style={{ backgroundColor: accent }}
                  aria-hidden
                />
              )}
              <span className="truncate text-row font-bold text-heading">
                {run.name}
              </span>
            </div>
            <Badge variant={toneBadge[view.tone]}>{view.label}</Badge>
          </div>

          {/* One wrapping row of facts, not four stacked lines: a run record is
              a log entry, and the facts fill the width left-to-right instead of
              spending ~30% of the card on padding between one-line rows. */}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-text-secondary">
            {/* Mission — the run's title is now its name, so the mission reads here as a fact. */}
            <span className="flex min-w-0 items-center gap-1.5" title={`Mission: ${run.mission}`}>
              <Crosshair className="size-3 shrink-0 text-text-tertiary" />
              <span className="truncate">{run.mission}</span>
            </span>

            <span className="flex min-w-0 items-center gap-1.5">
              {/* The agent's harness mark, greyed — a subtle identity glyph in place of the
                  generic chip (the harness name still reads in words below). */}
              <HarnessGlyph
                kind={run.source_agent}
                muted
                className="[&_svg]:!size-4"
              />
              <span className="truncate" title={agentName}>
                {agentName}
              </span>
            </span>

            {harness && (
              <span className="flex min-w-0 items-center gap-1.5 text-text-tertiary">
                <Terminal className="size-3 shrink-0" />
                <span className="truncate">{harness}</span>
              </span>
            )}

            <span className="flex items-center gap-1.5 tabular-nums">
              <Clock className="size-3 shrink-0 text-text-tertiary" />
              {shortTime(run.created_at)}
            </span>

            {(elapsed !== null || run.budget_seconds > 0) && (
              <span
                className="flex items-center gap-1.5 tabular-nums"
                title="Elapsed / budget"
              >
                <Timer className="size-3 shrink-0 text-text-tertiary" />
                <span className="truncate">
                  {elapsed !== null && formatElapsed(elapsed)}
                  {elapsed !== null && run.budget_seconds > 0 && " / "}
                  {run.budget_seconds > 0 && formatBudget(run.budget_seconds)}
                </span>
              </span>
            )}
          </div>

          {/* The whole card is the link; this line names the destination rather
              than repeating the hover affordance at full weight. */}
          <span className="mt-auto flex items-center gap-1 pt-2 text-label uppercase text-text-tertiary transition-colors group-hover:text-primary">
            {view.action.label}
            <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
          </span>
        </Card>
      </Link>

      {deletable && (
        <>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Delete run"
            title="Delete run"
            onClick={(e) => {
              // Belt-and-braces: the button is outside the Link, but never navigate.
              e.preventDefault();
              e.stopPropagation();
              setConfirmDelete(true);
            }}
            // Ghost's hover is amber; this one action is destructive, so the hover ink is
            // overridden here rather than by a new variant. The reveal stays local too.
            className="absolute right-2 top-2 z-10 text-text-tertiary opacity-0 transition-opacity hover:text-err focus-visible:opacity-100 group-hover:opacity-100"
          >
            <Trash2 className="size-3.5" />
          </Button>
          {confirmDelete && (
            <DeleteRunInline
              runId={run.run_id}
              runName={run.name}
              onCancel={() => setConfirmDelete(false)}
              onDeleted={() => {
                setConfirmDelete(false);
                pushToast({ tone: "info", title: "Run deleted" });
              }}
            />
          )}
        </>
      )}
    </div>
  );
}
