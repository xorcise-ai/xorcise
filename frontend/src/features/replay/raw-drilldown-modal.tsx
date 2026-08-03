"use client";

import { useEffect, useState } from "react";
import { Dialog } from "@/components/ui/dialog";
import { SkeletonRows } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import type { AgentEvent } from "@/lib/api/types";

interface RawEventResponse {
  event_id: string;
  raw_ref: { signal?: "trace" | "log" } | null;
  spans: unknown[];
  logs: unknown[];
}

/** `event.id` derives from a base64 span id, which may contain '/'; preserve the
 * segment structure but percent-encode each segment individually. */
function rawPath(runId: string, eventId: string): string {
  const segments = eventId.split("/").map(encodeURIComponent).join("/");
  return `/runs/${encodeURIComponent(runId)}/events/${segments}/raw`;
}

/** Drill-down modal: shows the source RAW OTLP span or log record(s). */
export function RawDrillDownModal({
  runId,
  event,
  open,
  onClose,
}: {
  runId: string;
  event: AgentEvent | null;
  open: boolean;
  onClose: () => void;
}) {
  const [data, setData] = useState<RawEventResponse | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !event) return;
    let cancelled = false;
    setLoading(true);
    setError(false);
    setData(null);
    api
      .get<RawEventResponse>(rawPath(runId, event.id))
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, event, runId]);

  if (!open || !event) return null;

  return (
    <Dialog open={open} onClose={onClose} title={`Raw · ${event.title}`} size="lg">
      {loading && (
        <div role="status" aria-label="Loading raw telemetry…">
          <SkeletonRows count={6} />
        </div>
      )}
      {error && <p className="text-body text-err">Couldn’t load the raw telemetry.</p>}
      {data && (
        <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-[rgba(0,0,0,0.3)] p-3 font-mono text-dense text-foreground">
          {JSON.stringify(data.raw_ref?.signal === "log" ? data.logs : data.spans, null, 2)}
        </pre>
      )}
    </Dialog>
  );
}
