"use client";

import { FileDown } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import { apiBaseUrl } from "@/lib/api/runtime-config";

// Download the run's RAW OTLP stream as JSONL (GET /runs/{id}/otlp.jsonl) — one OTLP/JSON
// envelope per line, verbatim as the agent streamed it, with no XORCISE framing. The file
// feeds straight into OTel tooling that reads the Collector's otlpjson file format. Unlike
// the report there is no grading gate: the evidence exists as soon as telemetry does.
//
// Same idiom as DownloadReport: a PLAIN <a download>, not a fetch → blob → object-URL dance —
// the server sets `Content-Disposition: attachment` with a stable filename, so the browser
// saves the file with zero JS and the link stays right-clickable from the static export at /ui.
export function DownloadTraces({
  runId,
  className,
}: {
  runId: string;
  className?: string;
}) {
  const href = `${apiBaseUrl()}/runs/${encodeURIComponent(runId)}/otlp.jsonl`;
  return (
    <a
      href={href}
      download
      // Same idiom as DownloadReport: the design system's small outline button applied by
      // class, because the element MUST stay a plain <a download> (see the note above).
      className={cn(
        buttonVariants({ variant: "outline", size: "sm" }),
        className,
      )}
    >
      <FileDown aria-hidden className="size-3.5 shrink-0" />
      OTLP Traces (.jsonl)
    </a>
  );
}
