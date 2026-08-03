import {
  useQuery,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  AgentEntry,
  AgentDeclaration,
  AgentHistoryEntry,
} from "@/lib/api/types";

/** The agent LIST. Per-name queries hang off the same prefix — see invalidateAgentList. */
const AGENTS_KEY = ["agents"] as const;

/**
 * Refresh the agent list and NOTHING else (`exact`).
 *
 * The ["agents"] prefix also covers per-name keys — notably ["agents", name, "history"].
 * A prefix invalidation refetches every ACTIVE one of those, and the mutations below are
 * exactly the ones that can retire a name (a rename or a delete), so the page still mounted
 * on the old name would immediately GET /agents/{old}/history and 404. Keeping the
 * invalidation exact means a dead name can never be resurrected by a refetch.
 *
 * Per-name history is still refreshed by mutations that change its CONTENT without touching
 * the name (useDeleteRun invalidates the prefix on purpose) — that is the safe direction.
 */
function invalidateAgentList(qc: QueryClient) {
  return qc.invalidateQueries({ queryKey: AGENTS_KEY, exact: true });
}

export function useAgents() {
  return useQuery({
    queryKey: AGENTS_KEY,
    queryFn: () => api.get<AgentEntry[]>("/agents"),
  });
}

/** Single agent derived from the list (the server has no GET /agents/{name}). */
export function useAgent(name: string) {
  return useQuery({
    queryKey: AGENTS_KEY,
    queryFn: () => api.get<AgentEntry[]>("/agents"),
    select: (agents) => agents.find((a) => a.name === name) ?? null,
  });
}

export function useAgentHistory(name: string) {
  return useQuery({
    queryKey: ["agents", name, "history"],
    queryFn: () =>
      api.get<AgentHistoryEntry[]>(
        `/agents/${encodeURIComponent(name)}/history`,
      ),
    enabled: !!name,
  });
}

export function useRegisterAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (decl: AgentDeclaration) =>
      api.post<AgentEntry>("/agents", decl),
    onSuccess: () => invalidateAgentList(qc),
  });
}

/**
 * CLI parity: `agent update`. PUT the full declaration to /agents/{name} —
 * `name` is the CURRENT lookup key; a differing `decl.name` renames the agent.
 */
export function useUpdateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, decl }: { name: string; decl: AgentDeclaration }) =>
      api.put<AgentEntry>(`/agents/${encodeURIComponent(name)}`, decl),
    onSuccess: () => invalidateAgentList(qc),
  });
}

export function useDeleteAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api.del(`/agents/${encodeURIComponent(name)}`),
    onSuccess: () => invalidateAgentList(qc),
  });
}
