import type { AgentEvent } from "@/lib/api/types";

const parseFiniteTime = (value: string | null | undefined): number | null => {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
};

/** Deterministic chronology within the agent's own clock domain.
 *
 * `received_at` is deliberately absent: receipt proves when XORCISE learned about an event, not
 * when the event happened. A delayed export must not move a prompt behind its response.
 */
export function compareAgentEvents(a: AgentEvent, b: AgentEvent): number {
  const aTs = parseFiniteTime(a.ts);
  const bTs = parseFiniteTime(b.ts);
  if (aTs !== bTs) {
    if (aTs == null) return 1;
    if (bTs == null) return -1;
    return aTs - bTs;
  }
  const signal = (a.raw_ref.signal ?? "trace").localeCompare(b.raw_ref.signal ?? "trace");
  if (signal !== 0) return signal;
  if (a.raw_ref.raw_seq !== b.raw_ref.raw_seq) {
    return a.raw_ref.raw_seq - b.raw_ref.raw_seq;
  }
  return a.id.localeCompare(b.id);
}

export function sortAgentEvents(events: readonly AgentEvent[]): AgentEvent[] {
  return [...events].sort(compareAgentEvents);
}

export type ReplayMergeEntry<A, I> =
  | { kind: "agent"; agent: A }
  | { kind: "infra"; infra: I };

/**
 * Merge two clock domains without treating export latency as occurrence time.
 *
 * Agent items must already be in producer/causal order. Infra items are ordered here on the
 * server clock. Receipt time is safe only as one-way evidence:
 *
 *   agent.received_at < infra.ts  =>  the agent event happened before the infra activity
 *   agent.received_at > infra.ts  =>  unknown (the export may simply have been delayed)
 *
 * Therefore an infra activity is inserted after the latest agent item that the server had
 * already received. Because that insertion is a boundary in the fixed agent sequence, an older
 * delayed prompt can never be separated from and placed behind its later response. Exact ties
 * keep the existing infra-first prerequisite rule. Anchorless infra activities sort last.
 */
export function mergeAgentAndInfra<A, I>(
  agents: readonly A[],
  infra: readonly I[],
  options: {
    agentReceipt: (agent: A) => string | null | undefined;
    infraTime: (infra: I) => string | null | undefined;
    infraSequence: (infra: I) => number;
  },
): ReplayMergeEntry<A, I>[] {
  const orderedInfra = [...infra].sort((a, b) => {
    const aTs = parseFiniteTime(options.infraTime(a));
    const bTs = parseFiniteTime(options.infraTime(b));
    if (aTs !== bTs) {
      if (aTs == null) return 1;
      if (bTs == null) return -1;
      return aTs - bTs;
    }
    return options.infraSequence(a) - options.infraSequence(b);
  });

  const buckets: I[][] = Array.from({ length: agents.length + 1 }, () => []);
  for (const systemItem of orderedInfra) {
    const systemTs = parseFiniteTime(options.infraTime(systemItem));
    if (systemTs == null) {
      buckets[agents.length].push(systemItem);
      continue;
    }
    let boundary = 0;
    for (let index = 0; index < agents.length; index += 1) {
      const receipt = parseFiniteTime(options.agentReceipt(agents[index]));
      // Strict comparison preserves infra-first behavior on an exact server-time tie.
      if (receipt != null && receipt < systemTs) boundary = index + 1;
    }
    buckets[boundary].push(systemItem);
  }

  const merged: ReplayMergeEntry<A, I>[] = [];
  for (let boundary = 0; boundary <= agents.length; boundary += 1) {
    merged.push(...buckets[boundary].map((item) => ({ kind: "infra" as const, infra: item })));
    if (boundary < agents.length) {
      merged.push({ kind: "agent", agent: agents[boundary] });
    }
  }
  return merged;
}
