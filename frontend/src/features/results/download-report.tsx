"use client";

import { FileDown } from "lucide-react";
import { cn } from "@/components/ui/cn";
import { apiBaseUrl } from "@/lib/api/runtime-config";

// Download the run report (GET /runs/{id}/report?format=md|html).
//
// Deliberately a PLAIN <a download>, not a fetch → blob → object-URL dance: the server already
// sets `Content-Disposition: attachment` with a sensible filename, so the browser saves the file
// with zero JS, zero memory copy, and no change to the JSON-only api client. It also means the
// link is right-clickable ("Copy link address") and works with the static export served at /ui.
//
// The href is resolved at RUNTIME via apiBaseUrl() so a remote server works too.
export function DownloadReport({
  runId,
  disabled = false,
  className,
}: {
  runId: string;
  /** Hidden while the run is still grading — there is no report to serve yet (202). */
  disabled?: boolean;
  className?: string;
}) {
  if (disabled) return null;
  const href = (format: "md" | "html") =>
    `${apiBaseUrl()}/runs/${encodeURIComponent(runId)}/report?format=${format}`;
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <ReportLink href={href("md")} label="Report (.md)" />
      <ReportLink href={href("html")} label="Report (.html)" />
    </div>
  );
}

function ReportLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      download
      className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border px-2 text-caption text-foreground transition-colors hover:border-[rgba(255,255,255,0.14)] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <FileDown aria-hidden className="size-3.5 shrink-0" />
      {label}
    </a>
  );
}
