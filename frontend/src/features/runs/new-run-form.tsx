"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Check, Cpu, Download, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/components/ui/cn";
import { Input } from "@/components/ui/input";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { useAgents } from "@/features/agents/queries";
import { useSystem } from "@/features/settings/queries";
import { HARNESSES } from "@/features/agents/harnesses";
import {
  useMissions,
  useMissionManifest,
  usePullMission,
} from "@/features/missions/queries";
import { PullProgressBlock, platformLabel } from "@/features/missions/mission-card";
import { titleCase } from "@/features/missions/labels";
import { errorDetail } from "@/lib/api/client";
import type { CatalogEntry, Intel } from "@/lib/api/types";
import { BudgetSlider } from "./budget-slider";
import { useCreateRun } from "./queries";

/** Human harness name for an agent's `kind`, or null when it's the generic fallback
 *  (report §11: "Harness, when available"). */
function harnessLabel(kind: string | null | undefined): string | null {
  if (!kind || kind === "generic") return null;
  return HARNESSES.find((h) => h.kind === kind)?.name ?? kind;
}

/** Compact minutes readout for the review card (the run contract stays in seconds). */
function budgetLabel(seconds: number): string {
  const m = Math.round(seconds / 60);
  return `${m} min`;
}

/** Everything a catalog row can be matched on by the filter box. */
function haystack(c: CatalogEntry): string {
  return [c.name, c.summary, c.specialty, c.proficiency, ...c.skills, ...c.technologies]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

/**
 * The single create-run experience. Both preselections arrive as query params from
 * whichever surface sent the operator here — `?agent=` from an agent card / agent
 * detail, `?mission=` from a mission — so those pages hand off to this page
 * instead of carrying their own cut-down modal. Agents are keyed by NAME (the
 * run-create contract's `agent` field), matching the select's option values.
 *
 * LAYOUT: the page fills its pane and never scrolls the shell — `flex h-full min-h-0
 * flex-col` with a fixed head and one bounded body. The body is three EQUAL columns whose
 * numbered steps read strictly left → right, top → bottom (no zig-zag): column 1 stacks
 * the two SELECTIONS — agent (step 1) over the mission roster (step 2, the single
 * unbounded scroller, `min-h-0 flex-1 overflow-y-auto`); column 2 stacks the selected
 * mission's brief (step 3) over the intel-disclosure decision (step 4, its own card);
 * column 3 stacks Configure (step 5 — name, budget) over Review & launch (step 6 — the
 * review of what will run, with the Start action LAST, at the end of the flow). Each step
 * is atomic: one card, one decision.
 *
 * Intel disclosure is its own step UNDER the brief, NOT part of the launch step: intel are
 * mission-scoped content, and keeping the run's only variable-length control out of the
 * launch column is what stops that column overflowing when a intel-heavy mission loads.
 *
 * PULL-BEFORE-START: `POST /runs` auto-pulls a library mission server-side, but it does
 * so INSIDE the create request — a multi-minute, progress-less wait. So when the chosen
 * mission isn't installed we run the catalog's job-based pull first
 * (`usePullMission` → 202 + polled job), render the same percent/bytes/phase/ETA block
 * the catalog renders, and create the run only once the job reports `installed`. The
 * server-side auto-pull stays as the fallback for every other caller (CLI, deep links).
 */
export function NewRunForm({
  initialAgent = "",
  initialMission = "",
}: {
  initialAgent?: string;
  initialMission?: string;
}) {
  const router = useRouter();
  const agents = useAgents();
  const missions = useMissions();
  const create = useCreateRun();
  const createDetail = errorDetail(create.error);

  const [agent, setAgent] = useState(initialAgent);
  const [mission, setMission] = useState(initialMission);
  const [budget, setBudget] = useState(600);
  const [name, setName] = useState("");
  const [filter, setFilter] = useState("");
  // Intel disclosure (runcontrol intel_policy): which of the mission's authored intel the agent may
  // request during the run. Defaults to ALL (initialised when the manifest loads) for back-compat
  // with pre-control runs; the operator pares it back with the per-intel checklist + All/None.
  const [pickedIntel, setPickedIntel] = useState<Set<string>>(new Set());
  // Set the moment the operator commits, so the whole form reads as one in-flight action
  // (pull → create → navigate) instead of a button that silently does nothing.
  const [starting, setStarting] = useState(false);
  // Key the pull off the SELECTED mission (a stable id), not a transient local "pullingId":
  // usePullMission's queryFn resolves the mission's active server job, so a pull already in
  // flight — started here then navigated away from, or started from the catalog — RE-ATTACHES and
  // shows its progress when the operator returns to (or reloads) this page. Keying off local state
  // lost it on unmount. The hook is inert until a mission is selected and only polls while a job
  // is actually running.
  const pull = usePullMission(mission);

  // Allow library missions too — a not-yet-installed mission is pulled on start,
  // so the operator needn't visit the catalog first.
  const selectable = missions.data ?? [];
  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? selectable.filter((c) => haystack(c).includes(q)) : selectable;
  }, [selectable, filter]);

  const selectedMission =
    selectable.find((c) => c.mission_id === mission) ?? null;

  // The selected mission's authored intel, so the operator can decide disclosure BEFORE the run
  // (the sealed manifest is unchanged — this only chooses which intel run-control will hand out).
  const manifest = useMissionManifest(mission);
  const intel = manifest.data?.intel ?? [];
  // Default the selection to ALL authored intel when a mission's manifest loads (and re-default
  // when the target mission changes). Keyed on the loaded manifest so it initialises once per
  // mission and never clobbers the operator's later toggles.
  useEffect(() => {
    setPickedIntel(new Set((manifest.data?.intel ?? []).map((h) => h.id)));
  }, [manifest.data]);
  // How many of the mission's intel are currently selected — drives the count, the All/None
  // disabled states, and the derived policy.
  const disclosedCount = intel.filter((h) => pickedIntel.has(h.id)).length;
  // intel_policy sent to POST /runs (runcontrol grammar): "all"/"none" at the extremes (stable +
  // back-compat), else the CSV of picked ids. Omitted when the mission has no intel.
  const intelPolicy: string | undefined =
    intel.length === 0
      ? undefined
      : disclosedCount === intel.length
        ? "all"
        : disclosedCount === 0
          ? "none"
          : intel
              .filter((h) => pickedIntel.has(h.id))
              .map((h) => h.id)
              .join(",");

  const pulled = pull.isSuccess; // the selected mission's pull job reports installed
  const needsPull = !!selectedMission && !selectedMission.installed && !pulled;

  // Platform honesty at the point of commitment (AS4/PS1). The daemon's native platform comes
  // from the system probe; every verdict degrades to silence when a side is unknown — a
  // pre-contract catalog or an unreachable daemon must not spray false warnings.
  const host = useSystem().data?.host_platform ?? null;
  const noNativePath = // nothing this host could execute at all — the server would refuse (409)
    !!selectedMission &&
    !selectedMission.installed &&
    !!host &&
    selectedMission.platforms.length > 0 &&
    !selectedMission.platforms.includes(host) &&
    !selectedMission.platforms.includes("linux/amd64");
  const platformWarning = (() => {
    const c = selectedMission;
    if (!c || noNativePath) return null;
    if (c.installed) {
      // The server already compared the INSTALLED platform against the daemon (`emulated`).
      if (c.emulated !== true) return null;
      return (
        `This install is ${platformLabel(c.platform ?? "non-native")}` +
        `${host ? `, not native ${platformLabel(host)}` : ""} — the run executes through ` +
        "Docker's emulation layer: functional, but slower and not validated natively."
      );
    }
    // Not installed yet: warn BEFORE the download, from the catalog's validated platforms.
    if (!host || c.platforms.length === 0 || c.platforms.includes(host)) return null;
    return (
      `No native ${platformLabel(host)} image exists for this mission (validated: ` +
      `${c.platforms.map(platformLabel).join(", ")}) — starting will pull the amd64 image ` +
      "and run it under emulation: functional, but slower and not validated natively here."
    );
  })();
  // A base-generation mismatch is refused server-side (409) — an enabled Start would only
  // bounce, so it is disabled WITH the reason (never silently).
  const incompatible = selectedMission?.compatible === false;
  const canSubmit = !!agent && !!mission && budget > 0 && !starting && !incompatible && !noNativePath;
  // Show the download panel whenever the selected mission is actually pulling (recoverable
  // across navigation), errored, or the operator has just committed to a not-yet-installed one.
  // `isCancelled` keeps the panel up after a cancel so the outcome is stated, rather than the whole
  // block vanishing and leaving the operator guessing whether it stopped.
  const showPull =
    !!selectedMission &&
    !selectedMission.installed &&
    (pull.isPulling || pull.isError || pull.isCancelled || starting);

  const createdRef = useRef(false);
  const autoStartedRef = useRef(false);

  const startRun = useCallback(async () => {
    if (createdRef.current) return;
    createdRef.current = true;
    try {
      const created = await create.mutateAsync({
        agent,
        mission,
        budget_seconds: budget,
        // Blank → the server mints the lazy "<mission> · <agent> #<n>" default.
        name: name.trim() || null,
        // Only send a policy when the mission actually has intel; otherwise leave it unset.
        ...(intelPolicy !== undefined ? { intel_policy: intelPolicy } : {}),
      });
      router.push(`/runs/live?id=${encodeURIComponent(created.run_id)}`);
    } catch {
      // The mutation's error state renders the alert; let the operator retry.
      createdRef.current = false;
      setStarting(false);
    }
  }, [agent, mission, budget, name, intelPolicy, create, router]);

  // The operator committed to starting AND the pull has finished → create the run they asked for.
  // Gated on `starting` so a pull that completes on its own (e.g. one kicked from the catalog)
  // never spuriously creates a run here. Once: a failed create surfaces its error and waits for the
  // operator rather than retrying in a loop (a failed mutation re-renders, re-firing this effect).
  useEffect(() => {
    if (!starting || !pulled || autoStartedRef.current) return;
    autoStartedRef.current = true;
    void startRun();
  }, [starting, pulled, startRun]);

  // A failed download hands control back rather than leaving a dead spinner.
  useEffect(() => {
    if (pull.isError) setStarting(false);
  }, [pull.isError]);

  /**
   * Abandon the download AND the run it was the first half of. A multi-GB image can take minutes,
   * so an operator who changes their mind needs a way out that does not involve waiting or
   * reloading the page.
   *
   * `starting` must be cleared too: it is what marks the form as committed, and the effect above
   * creates the run as soon as the pull reports `installed`. Leaving it set would strand the form
   * in a committed state with nothing in flight — and if the worker finished installing before it
   * observed the cancel, it would go on to start the very run that was just called off.
   * `autoStartedRef` resets so a later retry can still auto-start.
   */
  const cancelDownload = () => {
    pull.cancel();
    setStarting(false);
    autoStartedRef.current = false;
  };

  function selectMission(id: string) {
    if (starting) return; // don't switch the target out from under an in-flight start
    setMission(id);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setStarting(true);
    if (needsPull) {
      // Start the pull (or re-attach / dedup to one already running — the server dedups by
      // mission). The effect above creates the run once the pull reports installed.
      pull.start();
      return;
    }
    await startRun();
  }

  const startLabel = pull.isPulling
    ? "Downloading…"
    : starting
      ? "Starting…"
      : needsPull
        ? "Download & Start Run"
        : "Start Run";

  return (
    <form
      onSubmit={submit}
      className="flex h-full min-h-0 flex-col gap-3 p-4"
    >
      <header className="shrink-0">
        <h1 className="text-lead text-heading">New run</h1>
        <p className="max-w-[68ch] text-dense leading-relaxed text-text-secondary">
          Pair an agent with a mission, set a time budget, then start the
          evaluation.
        </p>
      </header>

      {/* Three equal columns, read left → right like the flow itself: SELECT (agent →
          mission), UNDERSTAND (brief → intel), COMMIT (configure → review → Start). */}
      <div className="grid min-h-0 flex-1 auto-rows-min gap-4 overflow-y-auto lg:auto-rows-auto lg:grid-cols-3 lg:overflow-hidden">
        {/* Column 1 — the selections: agent (1) on top, the mission roster (2) beneath. */}
        <div className="flex min-h-0 flex-col gap-3">
          <Step step={1} title="Select agent" className="shrink-0">
            {/* A roster of cards (like the mission list), not a dropdown — each row shows the
                agent's harness + model up front, so no summary panel is needed. min-h reserves
                ~3 rows in every state so the card never resizes as agents load. */}
            {(agents.data ?? []).length === 0 && agents.isLoading ? (
              <div
                role="status"
                aria-label="Loading agents…"
                className="min-h-24"
              >
                <SkeletonRows count={3} />
              </div>
            ) : (agents.data ?? []).length === 0 ? (
              <div className="flex min-h-24 items-center justify-center rounded-md border border-dashed border-border p-3 text-center text-caption text-text-tertiary">
                No agents registered — connect one from the Agents page.
              </div>
            ) : (
              <ul className="max-h-48 min-h-24 space-y-0.5 overflow-y-auto rounded-md border border-border bg-background p-1">
                {(agents.data ?? []).map((a) => {
                  const active = a.name === agent;
                  const harness = harnessLabel(a.kind);
                  return (
                    <li key={a.id}>
                      <button
                        type="button"
                        aria-label={a.name}
                        aria-pressed={active}
                        disabled={starting}
                        onClick={() => setAgent(a.name)}
                        data-active={active}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-dense transition-colors",
                          "disabled:cursor-not-allowed disabled:opacity-60",
                          active
                            ? "bg-primary/10 text-heading"
                            : "text-text-secondary hover:bg-card",
                        )}
                      >
                        <Cpu
                          className="size-3 shrink-0 text-text-tertiary"
                          aria-hidden
                        />
                        <span className="truncate font-medium">{a.name}</span>
                        <span className="ml-auto flex shrink-0 items-center gap-2 text-caption text-text-tertiary">
                          {harness && (
                            <span className="uppercase tracking-wide">
                              {harness}
                            </span>
                          )}
                          {a.model && (
                            <span
                              className="max-w-28 truncate"
                              title={a.model}
                            >
                              {a.model}
                            </span>
                          )}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </Step>

          <Step
            step={2}
            title="Select mission"
            className="min-h-0 flex-1"
            aside={
              <>
                <span className="text-caption tabular-nums text-text-tertiary">
                  {visible.length}/{selectable.length}
                </span>
                <Input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  aria-label="Filter missions"
                  placeholder="Filter…"
                  className="h-7 w-40"
                />
              </>
            }
            bodyClassName="flex min-h-0 flex-col"
          >
            {/* The roster — the ONE unbounded scroller on the page. */}
            {selectable.length === 0 && missions.isLoading ? (
              <div role="status" aria-label="Loading missions…">
                <SkeletonRows count={5} />
              </div>
            ) : selectable.length === 0 ? (
              <p className="text-caption text-text-tertiary">
                No missions available — ingest a bundle or connect the remote
                catalog.
              </p>
            ) : (
              <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto rounded-md border border-border bg-background p-1">
                {visible.map((c) => {
                  const active = c.mission_id === mission;
                  return (
                    <li key={c.mission_id}>
                      <button
                        type="button"
                        aria-label={c.name}
                        aria-pressed={active}
                        disabled={starting}
                        onClick={() => selectMission(c.mission_id)}
                        data-active={active}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-dense transition-colors",
                          "disabled:cursor-not-allowed disabled:opacity-60",
                          active
                            ? "bg-primary/10 text-heading"
                            : "text-text-secondary hover:bg-card",
                        )}
                      >
                        <span
                          className={cn(
                            "size-1.5 shrink-0 rounded-full",
                            c.installed ? "bg-ok" : "bg-primary",
                          )}
                          aria-hidden
                        />
                        <span className="truncate">{c.name}</span>
                        <span className="ml-auto flex shrink-0 items-center gap-2 text-caption uppercase tracking-wide text-text-tertiary">
                          {c.specialty && <span>{c.specialty}</span>}
                          {c.proficiency && <span>{c.proficiency}</span>}
                          {!c.installed && <span>library</span>}
                        </span>
                      </button>
                    </li>
                  );
                })}
                {visible.length === 0 && (
                  <li className="px-2 py-1.5 text-caption text-text-tertiary">
                    No missions match “{filter}”.
                  </li>
                )}
              </ul>
            )}
          </Step>
        </div>

        {/* Column 2 — understand the target before configuring: the mission's brief (3),
            then the intel-disclosure decision (4) as its own step card beneath it. */}
        <div className="flex min-h-0 flex-col gap-3">
          <Step
            step={3}
            title="Mission info"
            className="min-h-0 flex-1"
            bodyClassName="flex min-h-0 flex-col"
          >
            <MissionDetails mission={selectedMission} />
          </Step>

          {/* Intel are mission-scoped content (graduated spoilers), so their step sits directly
              under the brief — and keeping the run's only variable-length control out of the
              launch column is what stops that column overflowing on a intel-heavy mission. */}
          <Step
            step={4}
            title="Select intel"
            className="shrink-0"
            bodyClassName="space-y-2"
            aside={
              selectedMission && intel.length > 0 ? (
                <>
                  {/* Live disclosed/total count — mirrors the Review's Intel row. The sr-only
                      sibling gives the bare "2 of 2" its subject for screen readers. */}
                  <span className="text-caption tabular-nums text-text-tertiary">
                    {disclosedCount} of {intel.length}
                  </span>
                  <span className="sr-only">intel disclosed to the agent</span>
                  <button
                    type="button"
                    onClick={() =>
                      setPickedIntel(new Set(intel.map((h) => h.id)))
                    }
                    disabled={starting || disclosedCount === intel.length}
                    className="rounded px-1.5 py-0.5 text-label uppercase text-text-secondary transition-colors hover:bg-raised hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    All
                  </button>
                  <button
                    type="button"
                    onClick={() => setPickedIntel(new Set())}
                    disabled={starting || disclosedCount === 0}
                    className="rounded px-1.5 py-0.5 text-label uppercase text-text-secondary transition-colors hover:bg-raised hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    None
                  </button>
                </>
              ) : undefined
            }
          >
            {/* Every state fills the same ~3-row min height, so picking a mission (or one
                without intel) never jarringly resizes the card. */}
            {!selectedMission ? (
              <div className="flex min-h-24 items-center justify-center rounded-md border border-dashed border-border p-3 text-center text-caption text-text-tertiary">
                Select a mission to choose which intel the agent may request.
              </div>
            ) : (
              <IntelDisclosure
                loading={!!mission && manifest.isLoading}
                intel={intel}
                picked={pickedIntel}
                onToggle={(id) =>
                  setPickedIntel((prev) => {
                    const next = new Set(prev);
                    if (next.has(id)) next.delete(id);
                    else next.add(id);
                    return next;
                  })
                }
                disabled={starting}
              />
            )}
          </Step>
        </div>

        {/* Column 3 — commit, as two atomic steps: Configure (name + budget), then Review &
            launch (the review of what will run, with Start LAST). */}
        <div className="flex min-h-0 flex-col gap-3 lg:overflow-y-auto">
          <Step
            step={5}
            title="Configure"
            bodyClassName="space-y-3"
            className="shrink-0"
          >
            <div>
              <label
                htmlFor="run-name"
                className="mb-1.5 block text-label uppercase text-text-tertiary"
              >
                Run name{" "}
                <span className="normal-case tracking-normal text-text-tertiary/70">
                  (optional)
                </span>
              </label>
              <Input
                id="run-name"
                aria-label="Run name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={starting}
                placeholder="Auto-named from mission + agent"
                maxLength={255}
              />
            </div>
            <div>
              <span className="mb-1.5 block text-label uppercase text-text-tertiary">
                Budget
              </span>
              <BudgetSlider
                seconds={budget}
                onChange={setBudget}
                disabled={starting}
              />
            </div>
          </Step>

          <Step
            step={6}
            title="Review & launch"
            bodyClassName="space-y-3"
            className="shrink-0"
          >
            <ReviewCard
              runName={name.trim()}
              agentName={agent}
              missionName={selectedMission?.name ?? mission}
              budget={budget}
              hasAgent={!!agent}
              hasMission={!!mission}
              intelSummary={
                intel.length === 0
                  ? null
                  : `${disclosedCount} of ${intel.length}`
              }
            />

            <div className="space-y-2 border-t border-border pt-3">
            {/* Non-blocking honesty (AS4): the operator may proceed, but knowingly. */}
            {platformWarning && !starting && (
              <p
                role="alert"
                data-testid="platform-warning"
                className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/[0.06] p-2.5 text-dense text-text-secondary"
              >
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
                <span>{platformWarning}</span>
              </p>
            )}
            {/* Blocking states carry their reason — a disabled button must never be a riddle. */}
            {incompatible && (
              <p
                role="alert"
                data-testid="incompatible-blocked"
                className="flex items-start gap-2 rounded-md border border-err/30 bg-err/[0.06] p-2.5 text-dense text-text-secondary"
              >
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-err" aria-hidden />
                <span>
                  This mission is not runnable on this XORCISE —{" "}
                  {selectedMission?.compat_hint ??
                    "it was built for a different base generation."}
                </span>
              </p>
            )}
            {noNativePath && (
              <p
                role="alert"
                data-testid="no-platform-blocked"
                className="flex items-start gap-2 rounded-md border border-err/30 bg-err/[0.06] p-2.5 text-dense text-text-secondary"
              >
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-err" aria-hidden />
                <span>
                  This mission has no image this host can execute (validated:{" "}
                  {selectedMission?.platforms.map(platformLabel).join(", ")}) — the server
                  would refuse the run.
                </span>
              </p>
            )}
            {showPull && (
              <div className="space-y-1.5 rounded-md border border-border bg-background p-3">
                <div className="flex items-center gap-1.5 text-label uppercase text-text-tertiary">
                  <Download className="size-3 shrink-0" aria-hidden />
                  Downloading mission
                </div>
                {pull.isError ? (
                  <p role="alert" className="text-dense text-err">
                    Download failed{pull.errorDetail ? `: ${pull.errorDetail}` : "."}{" "}
                    Try again.
                  </p>
                ) : pull.isCancelled ? (
                  <p className="text-dense text-text-secondary">
                    Download cancelled — the run was not started.
                  </p>
                ) : (
                  <>
                    <PullProgressBlock pull={pull} />
                    {/* type="button" is load-bearing: inside a <form> a bare button submits, so
                        cancelling would re-commit the very run it is meant to call off. */}
                    {pull.canCancel && (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-6 px-2 text-caption"
                        onClick={cancelDownload}
                        disabled={pull.isCancelling}
                      >
                        {pull.isCancelling ? "Cancelling…" : "Cancel download"}
                      </Button>
                    )}
                  </>
                )}
                {pulled && (
                  <p className="text-caption text-text-secondary">
                    Installed — starting the run…
                  </p>
                )}
              </div>
            )}

            {needsPull && !showPull && (
              <p className="text-caption text-text-tertiary">
                Not installed yet — XORCISE downloads it first and shows the
                progress here.
              </p>
            )}

            {create.isError && (
              <p role="alert" className="text-dense text-err">
                {/* Prefer the server's own words. It answers a 503 with the cause AND
                    the command that fixes it; substituting "please try again" hid a
                    stopped control plane behind advice that could never work. */}
                {createDetail ? (
                  <>Couldn’t start the run — {createDetail}</>
                ) : (
                  <>Couldn’t start the run. Please try again.</>
                )}
              </p>
            )}

              <Button type="submit" className="w-full" disabled={!canSubmit}>
                {starting && (
                  <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />
                )}
                {startLabel}
              </Button>
            </div>
          </Step>
        </div>
      </div>
    </form>
  );
}

/** A numbered step: the page's one structural unit, so all five share a column rhythm
 *  (§11 guided flow). `bodyClassName` lets a step own its inner layout — the mission
 *  step makes its list the flexible region. */
function Step({
  step,
  title,
  aside,
  bodyClassName,
  className,
  children,
}: {
  step: number;
  title: string;
  aside?: ReactNode;
  bodyClassName?: string;
  /** Extra classes on the Card — the rail steps pass `shrink-0` so they keep their natural height
   *  and the rail scrolls, rather than being squeezed until their content spills the card border. */
  className?: string;
  children: ReactNode;
}) {
  return (
    <Card className={cn("flex min-h-0 flex-col p-3", className)}>
      <div className="mb-2 flex shrink-0 items-center gap-2">
        <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/15 text-caption font-semibold text-primary">
          {step}
        </span>
        <h2 className="text-body font-semibold text-heading">{title}</h2>
        {aside && (
          <div className="ml-auto flex shrink-0 items-center gap-2">{aside}</div>
        )}
      </div>
      <div className={cn("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </Card>
  );
}

/** The selected mission's brief — the body of step 3 (Mission info) — so the description
 *  reads at full size and the specialty/skills are visible before committing. Scrolls
 *  independently of the roster; a dashed placeholder holds the space until a mission is
 *  picked. Intel disclosure is NOT here — it is its own step (Select intel) beneath this card. */
function MissionDetails({ mission: c }: { mission: CatalogEntry | null }) {
  // A long brief is clamped so it can't dominate the column; the operator expands it on demand.
  // Length-gated (not layout-measured) so the toggle only ever appears when there is actually
  // hidden text — content is never clamped away without a way to reveal it.
  const [expanded, setExpanded] = useState(false);
  const longSummary = (c?.summary?.length ?? 0) > 240;
  if (!c)
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center rounded-md border border-dashed border-border p-4 text-center text-caption text-text-tertiary">
        Select a mission to read its brief.
      </div>
    );
  return (
    <div
      data-testid="mission-brief"
      className="min-h-0 flex-1 space-y-3 overflow-y-auto rounded-md border border-border bg-background p-3"
    >
      <div className="space-y-1.5">
        <div className="flex items-start justify-between gap-2">
          <p className="text-body font-semibold leading-tight text-heading">
            {c.name}
          </p>
          <Badge variant={c.installed ? "ok" : "default"} className="shrink-0">
            {c.installed ? "installed" : "pulls on start"}
          </Badge>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {c.specialty && (
            <Badge variant="info" className="capitalize">
              {titleCase(c.specialty)}
            </Badge>
          )}
          {c.proficiency && (
            <Badge variant="muted" className="capitalize">
              {titleCase(c.proficiency)}
            </Badge>
          )}
          {c.type && (
            <Badge variant="muted" className="uppercase">
              {c.type}
            </Badge>
          )}
          <Badge variant="muted">
            {c.source === "your_own" ? "Your own" : "Library"}
          </Badge>
        </div>
      </div>
      {c.summary ? (
        <div className="space-y-1">
          <p
            className={cn(
              "text-dense leading-relaxed text-text-secondary",
              longSummary && !expanded && "line-clamp-7",
            )}
          >
            {c.summary}
          </p>
          {longSummary && (
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => setExpanded((v) => !v)}
              className="text-label uppercase text-primary transition-colors hover:text-primary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {expanded ? "Show less" : "Show more"}
            </button>
          )}
        </div>
      ) : (
        <p className="text-caption text-text-tertiary">
          No description provided for this mission.
        </p>
      )}
      {c.skills.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-label uppercase text-text-tertiary">
            Skills
          </p>
          <div className="flex flex-wrap gap-1">
            {c.skills.map((s) => (
              <span
                key={s}
                className="rounded-full border border-primary/25 bg-primary/10 px-2 py-0.5 text-caption font-medium text-primary"
              >
                {titleCase(s)}
              </span>
            ))}
          </div>
        </div>
      )}
      {c.technologies.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-label uppercase text-text-tertiary">
            Technologies
          </p>
          <div className="flex flex-wrap gap-1">
            {c.technologies.map((t) => (
              <span
                key={t}
                className="rounded border border-border bg-raised px-1.5 py-0.5 font-mono text-caption text-text-secondary"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Compact final review of Agent / Mission / Budget (§11.3) — the body of the
 *  "Review & launch" step, whose header carries the title (no inner card or caption).
 *  Rows show a placeholder until the operator has made each required selection. */
function ReviewCard({
  runName,
  agentName,
  missionName,
  budget,
  hasAgent,
  hasMission,
  intelSummary,
}: {
  runName: string;
  agentName: string;
  missionName: string;
  budget: number;
  hasAgent: boolean;
  hasMission: boolean;
  /** "N of M" intel disclosed, or null when the mission has none (row omitted). */
  intelSummary: string | null;
}) {
  return (
    <dl className="space-y-1.5 text-dense">
        {/* Blank name → the server auto-names it; say so rather than showing "Not selected". */}
        <ReviewRow
          label="Name"
          value={runName || null}
          placeholder="Auto-named"
        />
        <ReviewRow label="Agent" value={hasAgent ? agentName : null} />
        <ReviewRow
          label="Mission"
          value={hasMission ? missionName : null}
        />
        <ReviewRow label="Budget" value={budgetLabel(budget)} />
        {intelSummary && <ReviewRow label="Intel" value={intelSummary} />}
    </dl>
  );
}

/**
 * Choose which of the mission's authored intel run-control may disclose to the agent (XOR intel
 * policy). Three modes: All (default), None, or a Custom id set. The sealed manifest is never
 * touched — this only sets the per-run policy string. The body of the "Select intel" step (its
 * caller already gates on a selected mission; the count + All/None live in that step's header),
 * so it only handles the manifest-loading, no-intel, and checklist states.
 */
function IntelDisclosure({
  loading,
  intel,
  picked,
  onToggle,
  disabled,
}: {
  loading: boolean;
  intel: Intel[];
  picked: Set<string>;
  onToggle: (id: string) => void;
  disabled: boolean;
}) {
  return (
    <div>
      {/* Every branch fills the same ~3-row min height as the step's placeholder, so switching
          missions (loading → intel / no intel) never jarringly resizes the card. */}
      {loading ? (
        <div className="flex min-h-24 items-center rounded-md border border-border bg-background p-3">
          <Skeleton variant="text" className="w-40" />
        </div>
      ) : intel.length === 0 ? (
        <div className="flex min-h-24 items-center justify-center rounded-md border border-dashed border-border p-3 text-center text-caption text-text-tertiary">
          This mission has no intel — nothing will be disclosed to the agent.
        </div>
      ) : (
        <div className="space-y-2">
          {/* The disclosure IS the checklist — pick exactly which intel run-control may hand out
              (the All/None shortcuts + count live in the step header above). Fixed-height
              internal scroller so a intel-heavy mission never grows the card past its neighbours.
              Rows reuse the app's checkbox affordance (facet-select) — a boxed Check, never a
              native input. Intel text is untrusted mission content: a plain single-line label
              (full text in the title), never interpreted. */}
          <div className="rounded-md border border-border bg-background">
            <ul className="max-h-40 min-h-24 space-y-0.5 overflow-y-auto p-1">
              {intel.map((h) => {
                const on = picked.has(h.id);
                return (
                  <li key={h.id}>
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked={on}
                      disabled={disabled}
                      onClick={() => onToggle(h.id)}
                      title={h.text || h.id}
                      className={cn(
                        "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-dense transition-colors",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        "hover:bg-raised disabled:cursor-not-allowed disabled:opacity-60",
                        on
                          ? "text-primary"
                          : "text-text-secondary hover:text-foreground",
                      )}
                    >
                      <span
                        aria-hidden
                        className={cn(
                          "flex size-3.5 shrink-0 items-center justify-center rounded-sm border",
                          on ? "border-primary bg-primary/20" : "border-border",
                        )}
                      >
                        {on && <Check className="size-2.5" />}
                      </span>
                      <span className="shrink-0 font-mono text-text-tertiary">
                        {h.id}
                      </span>
                      <span className="truncate">{h.text}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

function ReviewRow({
  label,
  value,
  placeholder = "Not selected",
}: {
  label: string;
  value: string | null;
  placeholder?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="text-text-tertiary">{label}</dt>
      <dd
        className={
          value
            ? "min-w-0 truncate text-right font-medium text-heading"
            : "text-right text-text-tertiary"
        }
        title={value ?? undefined}
      >
        {value ?? placeholder}
      </dd>
    </div>
  );
}
