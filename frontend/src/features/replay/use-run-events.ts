import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { AgentEvent, RunEventsView } from "@/lib/api/types";

export interface RunEventsMeta {
  sourceAgent: string;
  adapterName: string;
  adapterVersion: string;
  fallback: boolean;
}

type EventCursor = { trace_seq: number; log_seq: number };

/** Poll GET /runs/{id}/events with independent trace/log cursors while `active`, accumulating events
 * deduped by `event.id` (the server page may re-include events; the id is stable). */
export function useRunEvents(runId: string, active: boolean) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [meta, setMeta] = useState<RunEventsMeta | null>(null);
  // Stall clock: client-time mark of the last poll that delivered FRESH telemetry. Client
  // clock on both sides of the comparison, so operator-vs-server clock skew can't fake or
  // hide a stall.
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const cursorRef = useRef<EventCursor>({ trace_seq: -1, log_seq: -1 });
  const seenRef = useRef<Set<string>>(new Set());

  const query = useQuery({
    queryKey: ["events", runId],
    queryFn: () => {
      const cursor = cursorRef.current;
      return api.get<RunEventsView>(
        `/runs/${encodeURIComponent(runId)}/events` +
          `?trace_since=${cursor.trace_seq}&log_since=${cursor.log_seq}`,
      );
    },
    refetchInterval: active ? 1500 : false,
    enabled: !!runId,
  });

  useEffect(() => {
    setEvents([]);
    setMeta(null);
    setLastEventAt(null);
    cursorRef.current = { trace_seq: -1, log_seq: -1 };
    seenRef.current = new Set();
  }, [runId]);

  useEffect(() => {
    const view = query.data;
    if (!view) return;
    const next = view.next_cursor ?? {
      trace_seq: view.next_since,
      // A response from an older server has only one coordinate; preserve legacy behavior.
      log_seq: view.next_since,
    };
    cursorRef.current = {
      trace_seq: Math.max(cursorRef.current.trace_seq, next.trace_seq),
      log_seq: Math.max(cursorRef.current.log_seq, next.log_seq),
    };
    setMeta({
      sourceAgent: view.source_agent,
      adapterName: view.adapter_name,
      adapterVersion: view.adapter_version,
      fallback: view.fallback,
    });
    const fresh = (view.events ?? []).filter((e) => !seenRef.current.has(e.id));
    if (fresh.length === 0) return;
    // The FIRST batch is backlog replay, not fresh arrival — seed the stall clock from the
    // newest server-side receipt time (clamped to now) so reloading an already-quiet run
    // doesn't reset its stall clock. Every later batch really did just arrive: stamp now.
    const firstBatch = seenRef.current.size === 0;
    if (firstBatch) {
      const receipts = fresh
        .map((e) => Date.parse(e.received_at ?? e.ts))
        .filter((t) => !Number.isNaN(t));
      const newest = receipts.length > 0 ? Math.max(...receipts) : Date.now();
      setLastEventAt(Math.min(newest, Date.now()));
    } else {
      setLastEventAt(Date.now());
    }
    fresh.forEach((e) => seenRef.current.add(e.id));
    setEvents((prev) => [...prev, ...fresh]);
  }, [query.data]);

  return {
    events,
    meta,
    lastEventAt,
    isLoading: query.isLoading,
    isError: query.isError,
  };
}
