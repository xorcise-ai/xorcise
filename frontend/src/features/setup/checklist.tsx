"use client";

import Link from "next/link";
import {
  CheckCircle2,
  Circle,
  AlertTriangle,
  ArrowUpRight,
  ArrowRight,
  Play,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { useSystem, useConfig } from "@/features/settings/queries";
import { deployedPlanes } from "@/features/settings/module-groups";
import { useAgents } from "@/features/agents/queries";
import { useMissions } from "@/features/missions/queries";

type State = "ok" | "todo" | "optional" | "checking";

interface Item {
  state: State;
  label: string;
  helpText: string;
  href?: string;
  cta?: string;
}

/** Shared status vocabulary: a small badge that carries the same colour
 *  language as the rest of the app (green = done, amber = attention, grey =
 *  neutral). Keeps each checklist row self-describing at a glance. */
const STATUS: Record<
  State,
  { label: string; variant: NonNullable<BadgeProps["variant"]> }
> = {
  ok: { label: "Ready", variant: "ok" },
  todo: { label: "Action needed", variant: "default" },
  optional: { label: "Not configured", variant: "muted" },
  checking: { label: "Checking", variant: "muted" },
};

/** Sort weight so incomplete/attention items float to the top (blocked tasks
 *  first), completed ones sink to the bottom. */
const RANK: Record<State, number> = { todo: 0, checking: 1, optional: 2, ok: 3 };

/** Stable incomplete-first ordering (preserves declaration order within a rank). */
function incompleteFirst(items: Item[]): Item[] {
  return items
    .map((item, i) => ({ item, i }))
    .sort((a, b) => RANK[a.item.state] - RANK[b.item.state] || a.i - b.i)
    .map(({ item }) => item);
}

/** A required item's state, showing a neutral "checking" while its source query
 *  is still loading so a cold load doesn't flash "todo" warnings. */
function reqItem(
  loading: boolean,
  ok: boolean,
  label: string,
  okText: string,
  todoText: string,
  extra: { href?: string; cta?: string } = {},
): Item {
  return {
    state: loading ? "checking" : ok ? "ok" : "todo",
    label,
    helpText: loading ? "Checking…" : ok ? okText : todoText,
    ...extra,
  };
}

/**
 * Readiness checklist — a doctor-style "are you ready to run?" view that adapts
 * to state. While setup is incomplete the remaining tasks are shown
 * prominently, blocked items first, each with an inline action. Once every
 * required gate passes it collapses to a single success banner (the next action —
 * Start a Run — emphasised) with the full diagnostic list tucked behind an
 * expander. Required items gate the first run; optional ones (judge, remote
 * catalog) only improve the experience. All derived from the shared queries;
 * reconciles with Settings → Modules (this is the getting-started lens).
 */
export function ReadinessChecklist({
  startHref = "/runs/new",
}: {
  startHref?: string;
}) {
  const system = useSystem();
  const config = useConfig();
  const agents = useAgents();
  const missions = useMissions();

  // No data yet and no error → still checking; avoids flashing "todo" warnings on a cold load.
  const sysLoading = !system.data && !system.isError;
  const agentsLoading = !agents.data && !agents.isError;
  const missionsLoading = !missions.data && !missions.isError;

  const planes = system.data?.planes ?? [];
  const docker = planes.find((p) => p.name === "docker");
  const dbReady = system.data
    ? system.data.db_schema === "head" || system.data.db_schema === "fresh"
    : false;
  const agentCount = agents.data?.length ?? 0;
  const installed = (missions.data ?? []).filter((c) => c.installed).length;
  const catalogConnected = system.data?.catalog.state === "connected";
  const missionAvailable = installed > 0 || catalogConnected;
  const judge = config.data?.judge.configured ?? false;

  const required: Item[] = incompleteFirst([
    reqItem(
      sysLoading,
      !system.isError,
      "XORCISE.CORE reachable",
      "The local XORCISE.CORE is responding.",
      "Start the stack with `xorcise up`.",
    ),
    reqItem(
      sysLoading,
      dbReady,
      "Database ready",
      "Schema is up to date.",
      "Run `xorcise db upgrade`.",
    ),
    reqItem(
      sysLoading,
      !!docker?.ok,
      "Docker available",
      "The Docker daemon is reachable.",
      "Runs need Docker — start the daemon.",
    ),
    reqItem(
      agentsLoading,
      agentCount > 0,
      "An agent is registered",
      `${agentCount} registered.`,
      "Declare your first agent.",
      { href: "/agents", cta: "Register" },
    ),
    reqItem(
      missionsLoading || sysLoading,
      missionAvailable,
      "A mission is available",
      installed > 0 ? `${installed} installed.` : "Pull from the connected remote, or ingest a bundle.",
      "Connect the remote catalog or ingest a bundle.",
      { href: "/missions", cta: "Browse" },
    ),
  ]);

  const optional: Item[] = incompleteFirst([
    {
      state: judge ? "ok" : "optional",
      label: "Judge model configured",
      helpText: judge
        ? "Runs are graded with the LLM judge."
        : "Deterministic evaluation is available, but model-based judging is disabled.",
      href: "/settings?focus=judge",
      cta: "Configure in Settings",
    },
    {
      state: catalogConnected ? "ok" : "optional",
      label: "Remote catalog connected",
      helpText: catalogConnected
        ? "The XORCISE library is available."
        : "Connect it to browse and pull missions.",
      href: "/settings",
      cta: "Configure in Settings",
    },
  ]);

  const allRequiredOk = required.every((i) => i.state === "ok");
  const recommendedLeft = optional.filter((i) => i.state === "optional").length;

  // A compact "here's your environment" summary for the ready banner (keep the summary
  // meaningful, not just a bare "you're ready"). All from the same queries — no new sources.
  // Modules this host is meant to run (see deployedPlanes) — never count an absent one as a
  // failure, or a correctly-configured single-role host reads as partly broken.
  const deployed = deployedPlanes(planes);
  const planesOk = deployed.filter((p) => p.ok).length;
  const readySummary = [
    `${agentCount} agent${agentCount === 1 ? "" : "s"}`,
    installed > 0
      ? `${installed} mission${installed === 1 ? "" : "s"} installed`
      : catalogConnected
        ? "catalog connected"
        : "mission ready",
    judge ? "judge configured" : "deterministic grading",
    planes.length ? `${planesOk}/${planes.length} modules healthy` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const lists = (
    <div className="space-y-3">
      <Card>
        <ul className="divide-y divide-border">
          {required.map((i) => (
            <ChecklistRow key={i.label} item={i} />
          ))}
        </ul>
      </Card>

      <h3 className="text-label uppercase text-text-tertiary">
        Recommended
      </h3>
      <Card>
        <ul className="divide-y divide-border">
          {optional.map((i) => (
            <ChecklistRow key={i.label} item={i} />
          ))}
        </ul>
      </Card>
    </div>
  );

  // Ready → one success banner + Start a Run, detail collapsed behind an expander
  // (keep the summary, collapse the list, avoid duplicate green states).
  if (allRequiredOk) {
    return (
      <section className="space-y-3">
        <Card className="border-ok/30 bg-ok/[0.06]">
          <CardContent className="space-y-2 py-3">
            <div className="flex flex-wrap items-center gap-3">
              <CheckCircle2 className="size-5 shrink-0 text-ok" />
              <p className="min-w-0 flex-1 text-body text-foreground">
                You’re ready to run. Start your first evaluation.
              </p>
              <Link
                href={startHref}
                className={buttonVariants({ size: "sm" }) + " shrink-0"}
              >
                <Play className="size-3.5" />
                Start a Run
              </Link>
            </div>
            <p className="pl-8 text-caption text-text-tertiary">{readySummary}</p>
          </CardContent>
        </Card>

        <details className="group">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-label uppercase text-text-tertiary transition-colors hover:text-foreground">
            <ArrowRight className="size-3 transition-transform group-open:rotate-90" />
            Diagnostic checklist
            {recommendedLeft > 0 && (
              <span className="text-text-secondary">
                · {recommendedLeft} recommended step{recommendedLeft === 1 ? "" : "s"}
              </span>
            )}
          </summary>
          <div className="mt-3">{lists}</div>
        </details>
      </section>
    );
  }

  return (
    <section className="space-y-3">
      <p className="max-w-[68ch] text-body text-text-secondary">
        Complete the required steps below to run your first evaluation.
      </p>
      {lists}
    </section>
  );
}

function ChecklistRow({ item }: { item: Item }) {
  const done = item.state === "ok";
  const Icon =
    item.state === "ok"
      ? CheckCircle2
      : item.state === "todo"
        ? AlertTriangle
        : Circle;
  const color =
    item.state === "ok"
      ? "text-ok"
      : item.state === "todo"
        ? "text-primary"
        : "text-text-tertiary";
  const status = STATUS[item.state];
  return (
    // Completed items read quieter than the ones still needing attention.
    <li className={"flex items-center gap-3 px-4 py-3" + (done ? " opacity-70" : "")}>
      <Icon className={`size-4 shrink-0 ${color}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className={"text-body " + (done ? "text-text-secondary" : "text-foreground")}>
            {item.label}
          </p>
          <Badge variant={status.variant}>{status.label}</Badge>
        </div>
        <p className="mt-2 max-w-[68ch] text-body text-text-tertiary">
          {item.helpText}
        </p>
      </div>
      {item.href && item.state !== "ok" && item.state !== "checking" && (
        <Link
          href={item.href}
          className="flex shrink-0 items-center gap-1 text-label uppercase text-primary transition-colors hover:text-foreground"
        >
          {item.cta}
          <ArrowUpRight className="size-3" />
        </Link>
      )}
    </li>
  );
}
