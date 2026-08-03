"use client";

import { Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { runPresentation } from "./run-state";
import type { RunEntry } from "@/lib/api/types";

/** Frontend-only Run History filters (report §10): free-text search + Status / Agent /
 *  Mission facets. Mirrors the catalog's MissionFilters shape so the two filter bars
 *  stay consistent. */
export interface RunFilters {
  query: string;
  status: string | null;
  agent: string | null;
  mission: string | null;
}

export const EMPTY_RUN_FILTERS: RunFilters = {
  query: "",
  status: null,
  agent: null,
  mission: null,
};

/** True when any filter would narrow the list — drives the tertiary "Clear filters" affordance
 *  (report §17: shown only when at least one filter is active). */
export function hasActiveRunFilters(f: RunFilters): boolean {
  return Boolean(f.query.trim() || f.status || f.agent || f.mission);
}

/** The §10 status label for a run (Running / Completed / Partial / Timeout / Failed / Terminal /
 *  Created) — the same vocabulary the cards show, so the Status facet matches the badges. */
export function runStatusLabel(run: RunEntry): string {
  return runPresentation(run.state, run.terminal_trigger).label;
}

/** Operator-facing agent name for a run (matches RunCard): the registered agent's name, else a
 *  short id — so search and the Agent facet key on what the operator actually sees. */
export function runAgentName(
  run: RunEntry,
  agentNameById: Map<string, string>,
): string {
  return agentNameById.get(run.agent_id) ?? run.agent_id.slice(0, 8);
}

/** Pure client-side narrowing of the run list by search text + facets. Search matches the
 *  mission OR agent (name and raw id). */
export function filterRuns(
  runs: RunEntry[],
  agentNameById: Map<string, string>,
  f: RunFilters,
): RunEntry[] {
  const q = f.query.trim().toLowerCase();
  return runs.filter((run) => {
    const agent = runAgentName(run, agentNameById);
    if (q) {
      const hay = `${run.mission} ${agent} ${run.agent_id}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (f.status && runStatusLabel(run) !== f.status) return false;
    if (f.agent && agent !== f.agent) return false;
    if (f.mission && run.mission !== f.mission) return false;
    return true;
  });
}

/** Canonical Status ordering for the facet (active first, terminal outcomes after). */
const STATUS_ORDER = [
  "Running",
  "Created",
  "Completed",
  "Partial",
  "Timeout",
  "Failed",
  "Terminal",
];

/** Distinct status labels present across the runs, in canonical order (unknown labels last). */
export function distinctStatuses(runs: RunEntry[]): string[] {
  const set = new Set(runs.map(runStatusLabel));
  return [...set].sort((a, b) => {
    const ia = STATUS_ORDER.indexOf(a);
    const ib = STATUS_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
  });
}

/** Distinct agent names present across the runs, sorted. */
export function distinctAgents(
  runs: RunEntry[],
  agentNameById: Map<string, string>,
): string[] {
  const set = new Set(runs.map((r) => runAgentName(r, agentNameById)));
  return [...set].sort((a, b) => a.localeCompare(b));
}

/** Distinct mission names present across the runs, sorted. */
export function distinctMissions(runs: RunEntry[]): string[] {
  const set = new Set(runs.map((r) => r.mission));
  return [...set].sort((a, b) => a.localeCompare(b));
}

export function RunFilterBar({
  filters,
  onChange,
  statuses,
  agents,
  missions,
}: {
  filters: RunFilters;
  onChange: (f: RunFilters) => void;
  statuses: string[];
  agents: string[];
  missions: string[];
}) {
  const active = hasActiveRunFilters(filters);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-text-tertiary" />
        <Input
          aria-label="Search runs"
          placeholder="Search by mission or agent…"
          value={filters.query}
          onChange={(e) => onChange({ ...filters, query: e.target.value })}
          className="w-64 max-w-full pl-7"
        />
      </div>

      <Facet
        label="Status"
        placeholder="All statuses"
        value={filters.status}
        options={statuses}
        onChange={(status) => onChange({ ...filters, status })}
      />
      <Facet
        label="Agent"
        placeholder="All agents"
        value={filters.agent}
        options={agents}
        onChange={(agent) => onChange({ ...filters, agent })}
      />
      <Facet
        label="Mission"
        placeholder="All missions"
        value={filters.mission}
        options={missions}
        onChange={(mission) => onChange({ ...filters, mission })}
      />

      {active && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onChange(EMPTY_RUN_FILTERS)}
        >
          <X className="size-3.5" />
          Clear filters
        </Button>
      )}
    </div>
  );
}

/** Shared single-value facet select — also used by the agents list's Harness / Model filters. */
export function Facet({
  label,
  placeholder,
  value,
  options,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string | null;
  options: string[];
  onChange: (v: string | null) => void;
}) {
  return (
    <select
      aria-label={label}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      className="h-8 rounded-md border border-input bg-card px-2 text-dense text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <option value="">{placeholder}</option>
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}
