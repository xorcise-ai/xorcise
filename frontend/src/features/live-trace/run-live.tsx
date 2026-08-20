"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Activity, Bot, Clock, Map as MapIcon, ListTree, ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Chip, ChipRow } from "@/components/ui/chip";
import { StatusDot } from "@/components/ui/dot";
import { StatTile, StatTileRow } from "@/components/ui/stat-tile";
import { cn } from "@/components/ui/cn";
import { Button, buttonVariants } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { NotFoundState } from "@/components/layout/not-found-state";
import { fullTime, formatDuration } from "@/lib/api/format";
import { useRun, useTerminateRun, useRunEnvironment } from "@/features/runs/queries";
import { useAgents } from "@/features/agents/queries";
import { useConfig } from "@/features/settings/queries";
import { isTerminal, runPresentation, type RunTone } from "@/features/runs/run-state";
import { STALL_TICK_MS, isStalled, secondsSince, useNow } from "@/features/runs/stall";
import { useUiStore } from "@/stores/ui";
import { useRunEvents } from "@/features/replay/use-run-events";
import { ReplayTimeline } from "@/features/replay/replay-timeline";
import { TimelineWaterfall } from "./timeline-waterfall";
import { TerrainMap } from "./terrain-map";
import { useRunTerrain } from "./use-run-terrain";
import { deriveInfraRows } from "./terrain-fold";
import { RunPromptCard, LaunchCommandCopy } from "./run-prompt-card";
import { BackendStatusBulb, connectionChips, CHIP_DOT_TONE } from "./run-status";
import { runPhase } from "./run-phase";

function elapsedSeconds(createdAt: string, completedAt?: string | null): number | null {
  const start = new Date(createdAt).getTime();
  if (Number.isNaN(start)) return null;
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  if (Number.isNaN(end)) return null;
  return Math.max(0, Math.floor((end - start) / 1000));
}

/** A run budget in seconds as a compact "10m" / "10m 30s" / "45s" (raw "600s" read as a bug). */
function formatBudget(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

/** §10/§17 status tone → the Badge variant carrying the same colour (shared with the Run cards). */
const toneBadge: Record<RunTone, "default" | "ok" | "err" | "muted"> = {
  amber: "default",
  green: "ok",
  red: "err",
  muted: "muted",
};

/** One cell of the header's fact strip.
 *
 *  A FLEX item, not a grid cell: the strip's length varies with run state (5–8 facts), and a
 *  fixed column count always orphaned the last one alone on a wide, empty second row. Flex items
 *  on a short final row grow to consume the width instead, so there is never a dead band.
 *  min-w-0 + truncate so a long mono value can't overflow into its neighbour — min-w-0 now comes
 *  from StatTile's own base, and the truncate is aimed at the tile's <dd> (StatTile truncates its
 *  label, but a fact strip's VALUE is the part that runs long: a model id, a full timestamp). */
const META_CELL = "flex-1 basis-32 [&_dd]:truncate";

/** One metadata fact in the header strip — the design system's StatTile at its `small` rung
 *  (this strip carries ids, timestamps and durations, not headline figures), plus the growth and
 *  truncation the strip needs. Omitted by the caller when empty. */
function MetaItem({
  label,
  value,
  title,
}: {
  label: string;
  /** ReactNode so the Mission cell can hand over a Link instead of rebuilding the tile. */
  value: React.ReactNode;
  /** Hover tooltip for a truncated value; defaults to the value when it is plain text. */
  title?: string;
}) {
  return (
    <StatTile
      className={META_CELL}
      label={label}
      value={value}
      title={title ?? (typeof value === "string" ? value : undefined)}
      small
    />
  );
}

/** `true` once the viewport matches `query` (SSR-safe: starts false, syncs after mount). */
function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    setMatches(mq.matches);
    const on = () => setMatches(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return matches;
}

/** A framed section — title strip + body — that wraps the trace sub-views. With `fill`, the card
 *  fills its parent's height and the body scrolls internally (used in the side-by-side split). */
function Section({
  icon: Icon,
  title,
  accent,
  children,
  fill = false,
  scrollBody = false,
  dense = false,
}: {
  icon: typeof Activity;
  title: string;
  accent?: React.ReactNode;
  children: React.ReactNode;
  fill?: boolean;
  /** The section body owns the column's single scroller (used by Terrain, whose map has a
   *  natural minimum height — without it a short pane CLIPS the zoom controls unreachably). */
  scrollBody?: boolean;
  /** Trim the header + body padding — for the fixed Timeline strip, so it yields height to the
   *  Terrain/Trace work area below. */
  dense?: boolean;
}) {
  return (
    <Card className={fill ? "flex h-full min-h-0 flex-col overflow-hidden" : undefined}>
      <CardHeader
        className={cn("shrink-0 flex-row items-center gap-2", dense ? "py-1.5" : "py-2")}
      >
        <Icon className="size-3.5 text-primary" />
        <CardTitle className="text-label uppercase text-text-tertiary">
          {title}
        </CardTitle>
        {accent}
      </CardHeader>
      {/* fill: a flex column that does NOT scroll — the child (e.g. ReplayTimeline) owns the
          single internal scroller, so the side-by-side pane never nests two scrollbars.
          scrollBody flips that: the body IS the scroller (for a child that can't shrink). */}
      <CardContent
        className={
          fill
            ? `flex min-h-0 flex-1 flex-col p-4 ${scrollBody ? "overflow-y-auto" : "overflow-hidden"}`
            : dense
              ? "px-4 pb-2.5 pt-0"
              : "p-4"
        }
      >
        {children}
      </CardContent>
    </Card>
  );
}

export function RunLive({ runId }: { runId: string | null }) {
  const run = useRun(runId ?? "");
  const terminal = run.data ? isTerminal(run.data) : false;
  const runEvents = useRunEvents(runId ?? "", !!run.data && !terminal);
  // Environment lifecycle (Starting / Ready / Failed / Released / None) as the SERVER observes
  // it — replaces the old sandbox_ref guess, which could not see a static mission or a dead env.
  const environment = useRunEnvironment(runId ?? "", !!run.data && !terminal);
  const terminate = useTerminateRun();
  const agents = useAgents();
  const cfg = useConfig();
  // No-model degradation notice: explains an empty mission plane when no terrain attribution
  // model is configured, instead of leaving it mysterious.
  const attributionOff = cfg.data ? !cfg.data.terrain.configured : false;
  // Click-to-replay: selecting a trace event highlights its terrain action on the map. Owned here
  // so it's shared between the two sections and survives either side's own polling.
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  // Reverse hover link: hovering a terrain node reports the trace events whose action
  // targeted it, so the Trace can highlight them at a lighter emphasis than the click-selection.
  const [hoveredEventIds, setHoveredEventIds] = useState<string[]>([]);
  // Per-span attribution status for the Trace view. Shares the terrain query with
  // TerrainMap (same ["terrain", runId] key → React Query dedupes, no extra fetch). `action` = an
  // applicable action that mapped an edge (from `actions`); `considered` = every event the model
  // has a verdict for, applicable OR NOT (from attribution.considered_event_ids). A considered id
  // absent from `action` renders "analyzed — no action" instead of a grey "pending" dot; `running`
  // drives the "attributing…" pulse on the still-unconsidered spans.
  // Agent-inactivity warning: the run is "stalled" when a previously-streaming, still-live run
  // has delivered no fresh telemetry for the operator's configured threshold. Suppressed while
  // the events query itself errors — an unreachable server is a different fault with its own
  // message. Dismissal is keyed to the stall clock's mark, so the banner re-arms by itself the
  // moment telemetry resumes (a later re-stall warns again).
  const stallThreshold = useUiStore((s) => s.stallThresholdSeconds);
  const now = useNow(STALL_TICK_MS);
  const staleSeconds = secondsSince(runEvents.lastEventAt, now);
  const stalled =
    !terminal &&
    runEvents.events.length > 0 &&
    !runEvents.isError &&
    isStalled(staleSeconds, stallThreshold);
  const [stallDismissedAt, setStallDismissedAt] = useState<number | null>(null);
  const stallBannerVisible = stalled && stallDismissedAt !== runEvents.lastEventAt;
  const terrain = useRunTerrain(runId ?? "", !!run.data && !terminal).terrain;
  const attribution = terrain?.attribution ?? null;
  const attributedActionIds = useMemo(() => {
    const s = new Set<string>();
    for (const u of terrain?.updates ?? []) {
      if (u.event_id) s.add(u.event_id);
    }
    return s;
  }, [terrain]);
  const consideredIds = useMemo(
    () => new Set(attribution?.considered_event_ids ?? []),
    [attribution],
  );
  // Deterministic infra activities (join, brief, artifact, telemetry, …) interleaved into the Trace
  // as clickable time-travel rows — they carry no agent span, so without this they were invisible
  // in the trace. Ordered by the same receipt-time clock the terrain map folds by.
  const infraRows = useMemo(() => deriveInfraRows(terrain?.updates ?? []), [terrain]);

  // Side-by-side Terrain|Trace split (wide displays only): a draggable divider sets the terrain's
  // width; it starts wider than the trace. Percentages, clamped so neither pane collapses.
  const wide = useMediaQuery("(min-width: 1280px)");
  // Below the wide breakpoint the work area becomes [Terrain | Trace] tabs (single-screen layout);
  // Trace first — it's where the live stream lands.
  const [activeTab, setActiveTab] = useState<"terrain" | "trace">("trace");
  const [terrainPct, setTerrainPct] = useState(56);
  const rowRef = useRef<HTMLDivElement>(null);
  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const row = rowRef.current;
    if (!row) return;
    const rect = row.getBoundingClientRect();
    const onMove = (ev: PointerEvent) => {
      const pct = ((ev.clientX - rect.left) / rect.width) * 100;
      setTerrainPct(Math.min(72, Math.max(32, pct)));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  if (!runId)
    return (
      <NotFoundState
        title="Run not found"
        message="No run specified."
        backHref="/runs"
        backLabel="Back to runs"
      />
    );
  if (run.isLoading)
    return (
      <div role="status" aria-label="Loading run…" className="space-y-4 p-6">
        <Skeleton variant="title" className="w-64" />
        <SkeletonRows count={6} />
      </div>
    );
  if (!run.data)
    return (
      <NotFoundState
        title="Run not found"
        message={`No run “${runId}”.`}
        backHref="/runs"
        backLabel="Back to runs"
      />
    );

  const r = run.data;
  const agentName =
    agents.data?.find((a) => a.id === r.agent_id)?.name ?? r.agent_id.slice(0, 8);
  // Shared §10/§17 status vocabulary + the state-appropriate action (View result / View Partial
  // Result), reused from the Run History cards so the two surfaces read identically.
  const view = runPresentation(r.state, r.terminal_trigger);
  const resultHref = `/runs/result?id=${encodeURIComponent(r.run_id)}`;
  // The timeout banner used to offer "Inspect Final Activity". It could not work: the handler only
  // ran `setActiveTab("trace")` below 1280px and did nothing at all above it, so it was either a
  // no-op or a re-select of the tab already showing — it never scrolled, despite the name. The
  // Trace is a tab away on the same page, and the banner still offers the two actions that leave
  // it (View partial result / Run again), so the control was removed rather than given new
  // behaviour nobody asked for.
  const phase = runPhase(
    r,
    runEvents.events.length,
    environment.data?.state,
    stalled ? staleSeconds : null,
  );
  const awaitingAgent = !terminal && runEvents.events.length === 0;
  // The environment is still coming up: the hand-off card must not read "Action required".
  const awaitingEnvironment = environment.data?.state === "starting";
  const elapsed = elapsedSeconds(r.created_at, r.completed_at);
  const startedAt = fullTime(r.created_at);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-hidden p-4">
      {/* ─── Run header — one compact card: identity + status + connection meta ─── */}
      <Card className="shrink-0">
        <CardContent className="space-y-2 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              {!terminal && (
                <span
                  className="size-2 motion-safe:animate-pulse rounded-full bg-ok"
                  aria-label="live"
                />
              )}
              <h1 className="truncate text-lead text-heading" title={r.name}>
                {r.name}
              </h1>
              <Badge variant={toneBadge[view.tone]}>{view.label}</Badge>
              <BackendStatusBulb phase={phase} compact />
              {/* Run ID — a debugging value, so its copy is icon-only: the labelled copy budget
                  in this row belongs to the launch command, not the id (§12, §17). */}
              <span className="flex min-w-0 items-center gap-1 rounded-md border border-border px-1.5 py-0.5">
                <span
                  className="max-w-40 truncate font-mono text-dense text-text-secondary"
                  title={r.run_id}
                >
                  {r.run_id}
                </span>
                <CopyButton
                  text={r.run_id}
                  idleLabel=""
                  copiedLabel=""
                  aria-label="Copy run ID"
                  title="Copy run ID"
                  variant="ghost"
                  size="sm"
                  className="h-5 gap-0 px-1 [&_svg]:size-3"
                />
              </span>
            </div>
            <div className="flex items-center gap-2">
              {/* Once the hand-off card has yielded its slot to the live trace the launch command
                  is still one click away — demoted to a ghost, never deleted (an agent that died
                  after one span must be relaunchable without re-reading the run). */}
              {!terminal && !awaitingAgent && <LaunchCommandCopy runId={r.run_id} />}
              {!terminal && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => terminate.mutate(r.run_id)}
                  disabled={terminate.isPending}
                >
                  {terminate.isPending ? "Terminating…" : "Terminate"}
                </Button>
              )}
              {/* Terminal → the result is always reachable (secondary action, §17). The timeout
                  case gets its own dedicated message + actions below, so it's excluded here to
                  avoid a duplicate button. */}
              {terminal && r.terminal_trigger !== "timeout" && (
                <Link
                  href={resultHref}
                  className={buttonVariants({ variant: "outline", size: "sm" })}
                >
                  View result
                  <ExternalLink className="size-3" />
                </Link>
              )}
            </div>
          </div>

          <Separator />

          {/* Connection facts as the design system's Chip — KEY · dot · value, the boxed readout
              the Figma run header draws. Harness sits with them rather than in the fact strip
              below: it names what this run is CONNECTED to (the chip's job), not a figure the
              operator scans. Harness carries no dot — it has no state to report. */}
          <ChipRow>
            <Chip label="Harness" value={r.source_agent} tone={null} />
            {connectionChips(r, runEvents.events.length, environment.data).map((c) => (
              <Chip
                key={c.label}
                label={c.label}
                value={c.value}
                tone={CHIP_DOT_TONE[c.tone]}
                title={c.title}
              />
            ))}
          </ChipRow>

          {/* One wrapping strip of facts — content-sized, so a 5-fact CREATED run and an
              8-fact terminal run both fill their rows with no orphaned cell. A real <dl>, because
              every cell is a term and its definition (StatTileRow supplies it). */}
          <StatTileRow className="gap-x-6 gap-y-2">
            {/* Identity metadata — the same canonical set the results page and the HTML/MD exports
                use: Mission (name + version, links to its brief), Agent (name + version), when it
                Started and how long it ran (Duration). */}
            <MetaItem
              label="Mission"
              title={`Mission: ${r.mission}`}
              value={
                <Link
                  href={`/missions/detail?id=${encodeURIComponent(r.mission)}`}
                  className="hover:underline"
                >
                  {r.mission} v{r.mission_version}
                </Link>
              }
            />
            <MetaItem label="Agent" value={`${agentName} v${r.agent_version}`} />
            {r.model && <MetaItem label="Model" value={r.model} />}
            <MetaItem label="Budget" value={formatBudget(r.budget_seconds)} />
            {startedAt && <MetaItem label="Started" value={startedAt} />}
            {elapsed != null && (
              <MetaItem label="Duration" value={formatDuration(elapsed)} />
            )}
          </StatTileRow>
        </CardContent>
      </Card>

      {/* ─── Timeout message (§12) — shown when the run hit its configured time limit ─── */}
      {r.terminal_trigger === "timeout" && (
        <Card className="shrink-0 border-err/30 bg-err/5 p-4">
          <p className="text-body font-semibold text-err">Run timed out</p>
          <p className="mt-2 max-w-[68ch] text-body text-text-secondary">
            The agent reached the configured time limit before completing the
            mission.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link
              href={resultHref}
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              View partial result
              <ExternalLink className="size-3" />
            </Link>
            <Link
              href="/runs/new"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              Run again
            </Link>
          </div>
        </Card>
      )}

      {/* ─── Agent-inactivity warning — the agent went quiet mid-run (crash / disconnect /
           guard-rail exit) and would otherwise burn silently to the budget timeout. A warning,
           not lifecycle management: the operator decides between Terminate and waiting out a
           legitimately slow tool call (Dismiss re-arms itself when telemetry resumes). ─── */}
      {stallBannerVisible && (
        <Card
          // role="status" (polite live region): the banner appears without focus, so SC 4.1.3
          // needs it announced — polite, not alert, since it stays on screen and isn't urgent
          // enough to interrupt mid-task speech.
          role="status"
          className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-err/30 bg-err/5 px-3 py-1.5"
          title={`No OTel export for ${formatDuration(staleSeconds ?? 0)} — the agent may have crashed or disconnected; the run keeps counting against its budget until it times out. Warn threshold ${formatDuration(stallThreshold)} — change it in Settings.`}
        >
          <p className="min-w-0 flex-1 text-dense">
            <span className="font-semibold text-err">Agent may have stopped</span>
            <span className="text-text-secondary">
              {" "}
              — no telemetry for {formatDuration(staleSeconds ?? 0)}; the budget keeps counting.
            </span>
          </p>
          <span className="flex shrink-0 items-center gap-1.5">
            <Button
              variant="outline"
              size="sm"
              onClick={() => terminate.mutate(r.run_id)}
              disabled={terminate.isPending}
            >
              {terminate.isPending ? "Terminating…" : "Terminate Run"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setStallDismissedAt(runEvents.lastEventAt)}
            >
              Dismiss
            </Button>
          </span>
        </Card>
      )}

      {/* ─── Timeline — a fixed strip, and only once there are events to plot. An empty
           timeline is not a state worth a card: it billed ~95px of the work area to render
           one line of "nothing yet" in exactly the state that needed the height most. ─── */}
      {runEvents.events.length > 0 && (
        <div className="shrink-0">
          <Section icon={Clock} title="Timeline" dense>
            {/* A single-line event timeline (legend + track + axis) — compact and fixed height,
                so it never steals the work area below. */}
            <div className="overflow-x-hidden">
              <TimelineWaterfall
                events={runEvents.events}
                infraRows={infraRows}
                selectedEventId={selectedEventId}
                onSelectEvent={setSelectedEventId}
              />
            </div>
          </Section>
        </div>
      )}

      {/* ─── Terrain + Trace ─── resizable side-by-side on wide displays; tabs below.
           The right pane is ONE slot with two occupants: while the run is waiting it holds the
           launch hand-off (the job in that state), and it swaps to the live trace in place the
           moment the first event lands. The hand-off is not a fourth fixed band above the work
           area — that is what starved the Terrain to 205px and clipped its controls. ─── */}
      {(() => {
        const terrainSection = (
          <Section icon={MapIcon} title="Terrain" fill scrollBody>
            <TerrainMap
              runId={r.run_id}
              active={!terminal}
              selectedEventId={selectedEventId}
              attributionOff={attributionOff}
              // fullscreen toggle — the split pane is fine for glancing, not for reading a
              // large graph; expanded, the same map instance fills the viewport.
              expandable
              onHoverEvents={setHoveredEventIds}
              onReturnToLive={() => setSelectedEventId(null)}
            />
          </Section>
        );
        const traceSection = (
          <Section
            icon={ListTree}
            title="Trace"
            fill
            accent={
              <span className="ml-auto flex items-center gap-3">
                {attribution && attribution.attributable > 0 && (
                  <span
                    className="inline-flex items-center gap-1.5 text-caption text-text-tertiary"
                    title="BYOM terrain attribution progress (attributed / attributable spans)"
                  >
                    {attribution.running && (
                      <StatusDot tone="primary" className="motion-safe:animate-pulse" />
                    )}
                    {attribution.running ? "attributing actions to terrain map…" : "attributed actions"} {attribution.attributed}/
                    {attribution.attributable}
                  </span>
                )}
                <span className="inline-flex items-center gap-1.5 text-caption text-text-tertiary">
                  <Bot className="size-3" />
                  {runEvents.events.length} event{runEvents.events.length === 1 ? "" : "s"}
                </span>
                {!terminal && (
                  <span className="inline-flex items-center gap-1 text-label uppercase text-ok">
                    <StatusDot tone="ok" className="motion-safe:animate-pulse" />
                    live
                  </span>
                )}
              </span>
            }
          >
            {runEvents.isError ? (
              <p className="text-body text-err">Couldn’t load the trace.</p>
            ) : (
              <ReplayTimeline
                runId={r.run_id}
                events={runEvents.events}
                meta={runEvents.meta}
                selectedEventId={selectedEventId}
                onSelectEvent={setSelectedEventId}
                hoveredEventIds={hoveredEventIds}
                attributedActionIds={attributedActionIds}
                consideredIds={consideredIds}
                attributing={!!attribution?.running}
                infraRows={infraRows}
                fill
              />
            )}
          </Section>
        );
        // The slot's pre-flight occupant: the launch hand-off, sized to the pane, with its own
        // single internal scroller and its copy action pinned above the payload.
        const workSection = awaitingAgent ? (
          <RunPromptCard runId={r.run_id} fill awaitingEnvironment={awaitingEnvironment} />
        ) : (
          traceSection
        );
        if (!wide)
          return (
            <Tabs
              value={activeTab}
              onValueChange={(v) => setActiveTab(v as "terrain" | "trace")}
              className="flex min-h-0 flex-1 flex-col gap-3"
            >
              <TabsList className="shrink-0">
                <TabsTrigger value="terrain">Terrain</TabsTrigger>
                <TabsTrigger value="trace">
                  Trace
                  {awaitingAgent && (
                    <StatusDot
                      tone="primary"
                      className="ml-1.5 inline-block motion-safe:animate-pulse"
                    />
                  )}
                </TabsTrigger>
              </TabsList>
              <TabsContent value="terrain" className="min-h-0 flex-1">
                {terrainSection}
              </TabsContent>
              <TabsContent value="trace" className="min-h-0 flex-1">
                {workSection}
              </TabsContent>
            </Tabs>
          );
        return (
          <div ref={rowRef} className="flex min-h-0 flex-1 items-stretch">
            <div style={{ width: `${terrainPct}%` }} className="min-w-0">
              {terrainSection}
            </div>
            <div
              role="separator"
              aria-label="resize terrain and trace"
              aria-orientation="vertical"
              onPointerDown={startResize}
              className="mx-1 w-1.5 shrink-0 cursor-col-resize rounded-full bg-border transition-colors hover:bg-primary/50"
            />
            <div style={{ width: `${100 - terrainPct}%` }} className="min-w-0 flex-1">
              {workSection}
            </div>
          </div>
        );
      })()}
    </div>
  );
}
