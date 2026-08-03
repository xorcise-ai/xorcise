"use client";

import Link from "next/link";
import { Bot, Swords, Play, CheckCircle2, Activity } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { useAgents } from "@/features/agents/queries";
import { useMissions } from "@/features/missions/queries";
import { useRuns } from "@/features/runs/queries";
import { useSystem } from "@/features/settings/queries";
import { deployedPlanes } from "@/features/settings/module-groups";
import { isTerminal } from "@/features/runs/run-state";

/**
 * At-a-glance overview: headline counts + a system-health glance. Reuses the
 * shared list queries (no new endpoints) so it stays in sync with each section.
 */
export function Overview() {
  const agents = useAgents();
  const missions = useMissions();
  const runs = useRuns();
  const system = useSystem();

  const installed = (missions.data ?? []).filter((c) => c.installed).length;
  const allRuns = runs.data ?? [];
  const active = allRuns.filter((r) => r.state === "active").length;
  const completed = allRuns.filter(isTerminal).length;
  // Count only modules this host actually runs — a `not_deployed` module is absent, not
  // broken, and counting it would show a healthy control-only host as "2/5" and red.
  const planes = deployedPlanes(system.data?.planes ?? []);
  const planesOk = planes.filter((p) => p.ok).length;
  // No data yet and no error → still checking. Avoids flashing "down" (red) during
  // the initial /api/system round trip on a cold load.
  const checking = !system.data && !system.isError;

  return (
    // The health strip rides in the SAME row as the counts (a 5th, wider cell)
    // instead of a second full-width band — one ~88px strip, not two.
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[repeat(4,minmax(0,1fr))_minmax(0,1.5fr)]">
      <Stat
        href="/agents"
        icon={Bot}
        n={agents.data?.length ?? 0}
        label="Agents"
      />
      <Stat
        href="/missions"
        icon={Swords}
        n={installed}
        label="Missions installed"
      />
      <Stat
        href="/runs"
        icon={Play}
        n={allRuns.length}
        label="Runs"
        sub={active > 0 ? `${active} active` : undefined}
      />
      {/* Counts every terminal run (completed, timed-out, failed…), so "Evaluated" is
          more accurate than "Completed" (report §4). */}
      <Stat
        href="/results"
        icon={CheckCircle2}
        n={completed}
        label="Evaluated"
      />
      <Card className="sm:col-span-2 lg:col-span-1">
        <CardContent className="flex h-full flex-wrap content-center items-center gap-x-5 gap-y-2 p-3">
          <span className="flex items-center gap-2 text-label uppercase text-text-tertiary">
            <Activity className="size-3.5 text-primary" />
            System
          </span>
          <Health
            label="Modules"
            value={
              planes.length
                ? planesOk === planes.length
                  ? "Healthy"
                  : `${planesOk}/${planes.length}`
                : "—"
            }
            ok={planes.length > 0 && planesOk === planes.length}
            loading={checking}
          />
          <Health
            label="Catalog"
            value={system.data?.catalog.state ?? "—"}
            ok={system.data?.catalog.state === "connected"}
            loading={checking}
          />
          <Health label="Topology" value={system.data?.topology ?? "—"} ok loading={checking} />
          <Link
            href="/settings"
            className="ml-auto text-label uppercase text-text-tertiary transition-colors hover:text-foreground"
          >
            Settings →
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({
  href,
  icon: Icon,
  n,
  label,
  sub,
}: {
  href: string;
  icon: typeof Bot;
  n: number;
  label: string;
  sub?: string;
}) {
  return (
    <Link href={href} className="group block">
      <Card className="h-full transition-colors group-hover:border-[rgba(255,255,255,0.14)]">
        <CardContent className="p-3">
          <div className="flex items-center gap-1.5 text-text-tertiary">
            <Icon className="size-3.5 text-primary" />
            <span className="truncate text-label uppercase">
              {label}
            </span>
          </div>
          <p className="mt-1 text-2xl font-bold tabular-nums text-heading">{n}</p>
          {sub && <p className="text-caption text-text-tertiary">{sub}</p>}
        </CardContent>
      </Card>
    </Link>
  );
}

function Health({
  label,
  value,
  ok,
  loading = false,
}: {
  label: string;
  value: string;
  ok: boolean;
  loading?: boolean;
}) {
  // While loading, show a muted (not red) dot + "checking…" so a cold load never
  // flashes "down" before the first /api/system response arrives.
  const dot = loading
    ? "bg-text-tertiary motion-safe:animate-pulse"
    : ok
      ? "bg-ok"
      : "bg-err";
  return (
    <span className="flex items-center gap-1.5 text-dense">
      <span className={"inline-block size-1.5 rounded-full " + dot} aria-hidden />
      <span className="text-text-tertiary">{label}</span>
      <span className="capitalize text-foreground">{loading ? "checking…" : value}</span>
    </span>
  );
}
