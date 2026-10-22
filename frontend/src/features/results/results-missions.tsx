"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Target } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Reveal } from "@/components/ui/reveal";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import { StatTile } from "@/components/ui/stat-tile";
import { pct } from "@/lib/api/format";
import type { RunScore, ScoreSummary } from "@/lib/api/types";
import { useAgents } from "@/features/agents/queries";
import { useMissions } from "@/features/missions/queries";
import { useRunScores, useScoreSummary } from "./queries";
import { asideLine } from "./run-glyph";
import { AgreementPlot } from "./agreement-plot";
import { RunKey } from "./run-key";
import { LoadingCards } from "./loading-cards";

/** How far apart the judge and the checks sit, in words. Within 8 points reads as agreement. */
export function gapWords(gap: number | null | undefined): string {
  if (gap == null) return "—";
  const pts = Math.round(Math.abs(gap) * 100);
  if (pts < 8) return `agree (within ${pts})`;
  return `judge ${pts} ${gap < 0 ? "below" : "above"} checks`;
}

const versionKey = (
  mission: string | null | undefined,
  version: string | null | undefined,
) => `${mission ?? ""}@${version ?? ""}`;

/**
 * "Missions" secondary view of the Results page (report §14): one card per mission VERSION, each
 * with a grader agreement plot. The deterministic checks and the judge's rubric belong to the
 * mission, and a new version can change both, so runs on an older version get a row of their
 * own card, marked as older, instead of being plotted against checks they never faced. How each run
 * counts is the server's (GET /results/summary, reporting.scores).
 */
export function ResultsByMission() {
  const versions = useScoreSummary("mission");
  const perAgent = useScoreSummary("mission_agent");
  const scores = useRunScores();
  const agents = useAgents();
  const missions = useMissions();

  const agentName = useMemo(() => {
    const m = new Map((agents.data ?? []).map((a) => [a.id, a.name]));
    return (id: string) => m.get(id) ?? id.slice(0, 8);
  }, [agents.data]);
  const missionName = useMemo(() => {
    const m = new Map((missions.data ?? []).map((c) => [c.mission_id, c.name]));
    return (slug: string) => m.get(slug) ?? slug;
  }, [missions.data]);

  const runsByVersion = useMemo(() => {
    const m = new Map<string, RunScore[]>();
    for (const r of scores.data ?? []) {
      const k = versionKey(r.mission, r.mission_version);
      const list = m.get(k);
      if (list) list.push(r);
      else m.set(k, [r]);
    }
    return m;
  }, [scores.data]);

  const agentsByVersion = useMemo(() => {
    const m = new Map<string, ScoreSummary[]>();
    for (const s of perAgent.data ?? []) {
      const k = versionKey(s.mission, s.mission_version);
      const list = m.get(k);
      if (list) list.push(s);
      else m.set(k, [s]);
    }
    for (const list of m.values())
      list.sort(
        (a, b) => (b.median ?? -1) - (a.median ?? -1) || b.scored - a.scored,
      );
    return m;
  }, [perAgent.data]);

  // Missions most recently run first; within a mission, its current version first, then older
  // versions newest first, so each mission's rows stay together.
  const rows = useMemo(() => {
    const latest = new Map<string, string>();
    for (const s of versions.data ?? []) {
      const prev = latest.get(s.mission ?? "");
      if (!prev || (s.last_run ?? "") > prev)
        latest.set(s.mission ?? "", s.last_run ?? "");
    }
    return [...(versions.data ?? [])].sort(
      (a, b) =>
        (latest.get(b.mission ?? "") ?? "").localeCompare(
          latest.get(a.mission ?? "") ?? "",
        ) ||
        (a.mission ?? "").localeCompare(b.mission ?? "") ||
        Number(b.current_version) - Number(a.current_version) ||
        (b.last_run ?? "").localeCompare(a.last_run ?? ""),
    );
  }, [versions.data]);

  const loading = versions.isLoading || scores.isLoading;
  return (
    <div className="space-y-4">
      {(versions.isError || scores.isError) && (
        <p className="text-body text-err">Couldn’t load results.</p>
      )}

      {loading && <LoadingCards />}

      {versions.data && rows.length === 0 && <EmptyMissions />}

      {rows.length > 0 && <RunKey view="plot" />}

      {rows.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {rows.map((s, i) => {
            const k = versionKey(s.mission, s.mission_version);
            return (
              <Reveal key={k} delay={i * 40}>
                <MissionVersionRow
                  summary={s}
                  name={missionName(s.mission ?? "")}
                  runs={runsByVersion.get(k) ?? []}
                  agents={agentsByVersion.get(k) ?? []}
                  agentName={agentName}
                />
              </Reveal>
            );
          })}
        </div>
      )}
    </div>
  );
}

function plotSummary(name: string, version: string, s: ScoreSummary): string {
  const head = `${name} v${version}: ${s.scored} scored ${s.scored === 1 ? "run" : "runs"}`;
  const gap =
    s.judge_gap != null ? `; ${gapWords(s.judge_gap)} on average` : "";
  const aside = asideLine(s);
  return `${head}${gap}${aside ? `; ${aside}, not plotted on the judge axis` : ""}.`;
}

function MissionVersionRow({
  summary,
  name,
  runs,
  agents,
  agentName,
}: {
  summary: ScoreSummary;
  name: string;
  runs: RunScore[];
  agents: ScoreSummary[];
  agentName: (id: string) => string;
}) {
  // `pinned` is the agent a chip was pressed for; `hovered` the one under the pointer or focus,
  // which wins while it lasts.
  const [pinned, setPinned] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const active = hovered ?? pinned;
  const byAgent = new Map(agents.map((a) => [a.agent_id ?? "", a]));
  const agentLabel = (id: string) => {
    const a = byAgent.get(id);
    if (!a) return agentName(id);
    return `${agentName(id)} · median ${pct(a.median)} · ${gapWords(a.judge_gap)}`;
  };
  const version = summary.mission_version ?? "";
  const older = summary.current_version === false;
  const aside = asideLine(summary);
  const models = summary.judge_models;
  return (
    <Card
      data-testid="mission-version-card"
      className={cn("flex h-full flex-col gap-2.5 p-3", older && "bg-raised")}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Target className="size-4 shrink-0 text-primary" aria-hidden />
        {/* Card head — 14px/700, matching CardTitle and the agent card head. */}
        <span className="truncate text-row font-bold text-heading">{name}</span>
        <Badge variant="muted">v{version}</Badge>
        {older && <Badge variant="warn">older</Badge>}
      </div>
      <AgreementPlot
        runs={runs}
        agentName={agentName}
        agentLabel={agentLabel}
        highlight={active}
        onHoverAgent={setHovered}
        summary={plotSummary(name, version, summary)}
      />
      {agents.length > 0 && (
        <AgentChips
          agents={agents}
          name={agentName}
          label={agentLabel}
          pinned={pinned}
          active={active}
          onPin={setPinned}
          onHover={setHovered}
        />
      )}
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2">
        <StatTile label="Median" value={pct(summary.median)} tone="primary" />
        <StatTile
          label="Scored"
          value={`${summary.scored} of ${summary.runs}`}
          small
        />
        <StatTile
          label="Judge vs checks"
          value={gapWords(summary.judge_gap)}
          small
          className="col-span-2"
        />
      </dl>
      <div className="space-y-1 text-caption text-text-tertiary">
        {aside && <p>{aside}: not in the median.</p>}
        {models.length > 1 && (
          <p className="text-warning">
            Judged by {models.length} models ({models.join(", ")}): their scores
            may not compare.
          </p>
        )}
        {models.length === 1 && <p>Judged by {models[0]}.</p>}
      </div>
      {/* Below the stats, not above the plot, so every card's plot sits at the same height. */}
      {older && (
        <p className="text-caption text-text-secondary">
          No longer installed. Its checks and rubric may differ from the
          current version’s, so its runs are kept apart.
        </p>
      )}
    </Card>
  );
}

/** No terminal runs yet — what / why / next (report §17). */
function EmptyMissions() {
  return (
    <Card className="flex flex-col items-center gap-3 p-6 text-center">
      <Target className="size-6 text-text-tertiary" aria-hidden />
      <div className="space-y-2">
        <p className="text-body font-bold text-heading">No results yet</p>
        <p className="mx-auto max-w-sm text-body text-text-secondary">
          No mission has a completed evaluation. Start a run and its scores will
          roll up here per mission.
        </p>
      </div>
      <Link href="/runs/new" className={cn(buttonVariants(), "mt-1")}>
        Start run
      </Link>
    </Card>
  );
}

/**
 * Every agent with runs on this version, as a row of chips under the plot: always shown, so the
 * plot's runs can be told apart without a pointer. Hovering or focusing a chip lights up that
 * agent's runs; pressing it keeps them lit until pressed again. Each chip's accessible name
 * carries the agent's median and judge gap, the numbers the plot shows by position.
 */
function AgentChips({
  agents,
  name,
  label,
  pinned,
  active,
  onPin,
  onHover,
}: {
  agents: ScoreSummary[];
  name: (id: string) => string;
  label: (id: string) => string;
  pinned: string | null;
  active: string | null;
  onPin: (id: string | null) => void;
  onHover: (id: string | null) => void;
}) {
  return (
    <div role="group" aria-label="Agents" className="flex flex-wrap gap-1.5">
      {agents.map((a) => {
        const id = a.agent_id ?? "";
        const on = pinned === id;
        return (
          <button
            key={id}
            type="button"
            aria-pressed={on}
            aria-label={label(id)}
            onClick={() => onPin(on ? null : id)}
            onMouseEnter={() => onHover(id)}
            onMouseLeave={() => onHover(null)}
            onFocus={() => onHover(id)}
            onBlur={() => onHover(null)}
            className={cn(
              "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-caption transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              on
                ? "border-primary/60 bg-primary/10 text-primary"
                : active === id
                  ? "border-border-hover bg-heading/5 text-heading"
                  : "border-border text-text-secondary hover:text-heading",
            )}
          >
            <span>{name(id)}</span>
            <span className="tabular-nums text-primary">{pct(a.median)}</span>
          </button>
        );
      })}
    </div>
  );
}
