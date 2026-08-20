"use client";

import Link from "next/link";
import { Bot, Swords, Play, CheckCircle2, Activity, ArrowRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { StatusDot, type DotTone } from "@/components/ui/dot";
import { StatTile, StatTileRow } from "@/components/ui/stat-tile";
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
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[repeat(4,minmax(0,1fr))_minmax(0,1.5fr)]">
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
          {/* StatusDot, the ATOM, rather than Chip, the molecule. Chip is the right
              component wherever there is a row's worth of width for it — the live-run
              header uses it exactly as Figma draws it. This cell is the FIFTH column of a
              five-up grid at lg, about 215px wide, so three bordered chips stack instead
              of sitting in a row: the card triples in height and the grid stretches all
              four count cards to match it. That directly contradicts this row's own rule
              two comments above — "one ~88px strip, not two". Sharing the dot keeps the
              status vocabulary identical without importing the padding. */}
          <dl className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
            <SystemFact
              label="Modules"
              value={healthValue(
                planes.length
                  ? planesOk === planes.length
                    ? "Healthy"
                    : `${planesOk}/${planes.length}`
                  : "—",
                checking,
              )}
              tone={healthTone(planes.length > 0 && planesOk === planes.length, checking)}
            />
            <SystemFact
              label="Catalog"
              value={healthValue(system.data?.catalog.state ?? "—", checking)}
              tone={healthTone(system.data?.catalog.state === "connected", checking)}
            />
            <SystemFact
              label="Topology"
              value={healthValue(system.data?.topology ?? "—", checking)}
              tone={healthTone(true, checking)}
            />
          </dl>
          <Link
            href="/settings"
            className="ml-auto flex items-center gap-1 -my-1.5 py-1.5 text-label uppercase text-text-tertiary transition-colors hover:text-foreground"
          >
            Settings
            <ArrowRight className="size-3" />
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
      <Card className="h-full transition-colors group-hover:border-border-hover">
        <CardContent className="p-3">
          {/* One tile per card, so the <dl> a StatTile requires is a single-child row. */}
          <StatTileRow>
            <StatTile
              className="flex-1"
              label={
                <span className="flex min-w-0 items-center gap-1.5">
                  <Icon className="size-3.5 shrink-0 text-primary" />
                  <span className="truncate">{label}</span>
                </span>
              }
              value={
                <>
                  {n}
                  {sub && (
                    <span className="block text-caption text-text-tertiary">
                      {sub}
                    </span>
                  )}
                </>
              }
            />
          </StatTileRow>
        </CardContent>
      </Card>
    </Link>
  );
}

// The System readouts are Chips — the design system names this exact site (Modules /
// Catalog / Topology) as the canonical Chip row, so the hand-rolled dot+key+value went.
// While loading they show a muted (not red) dot + "checking…" so a cold load never
// flashes "down" before the first /api/system response arrives.
/** KEY · dot · value, at the Chip's typography but without its box. */
function SystemFact({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: DotTone;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <dt className="text-label uppercase text-text-tertiary">{label}</dt>
      <StatusDot tone={tone} />
      {/* capitalize so the API's "connected"/"local" read as words, not as ids. */}
      <dd className="text-dense capitalize text-foreground">{value}</dd>
    </div>
  );
}

function healthTone(ok: boolean, loading: boolean): DotTone {
  return loading ? "muted" : ok ? "ok" : "err";
}

function healthValue(value: string, loading: boolean) {
  return loading ? "checking…" : value;
}
