"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, ScanSearch, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { errorDetail } from "@/lib/api/client";
import type { PruneReport } from "@/lib/api/types";
import { formatBytes, usePruneImages } from "./queries";

/** The server's `reason`, worded for the operator. An unknown reason is shown as sent. */
const REASON_LABEL: Record<string, string> = {
  superseded: "Superseded by a newer version",
  "not installed": "Mission not installed",
};

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The confirmed images the server left alone because they were no longer unused. */
function keptSentence(report: PruneReport): string | null {
  const kept = report.skipped.length;
  if (kept === 0) return null;
  return kept === 1
    ? "1 image you confirmed was no longer unused, so it was kept."
    : `${kept} images you confirmed were no longer unused, so they were kept.`;
}

function appliedSummary(report: PruneReport): string {
  const found = report.candidates.length;
  const failed = report.failed.length;
  const kept = keptSentence(report);
  return [
    found === 0
      ? "Nothing to remove."
      : `Removed ${report.removed.length} of ${plural(found, "image", "images")}.`,
    failed > 0 ? `${plural(failed, "image was", "images were")} not removed.` : null,
    kept,
  ]
    .filter(Boolean)
    .join(" ");
}

/**
 * Reclaim disk from mission images nothing uses (CLI parity: `xorcise mission prune`).
 *
 * "Check" runs the server's dry run and lists each candidate with its reason and size. Removing
 * them needs an explicit confirmation, which sends `apply: true` with the ids of the images on
 * screen. The server re-plans at that moment and removes only those still unused, so the
 * confirmation covers exactly what can go. The applied report (what it removed, each image it
 * could not, and each confirmed image it kept) replaces the dry run. A refusal (409 when this
 * server does not manage Docker images, 503 when Docker is unreachable) is shown in the
 * server's own words, never as an empty report.
 */
export function PruneImagesPanel() {
  const prune = usePruneImages();
  const [confirming, setConfirming] = useState(false);
  // Held here, not read from prune.data: a new request clears the mutation's data, and the
  // confirm dialog must keep the dry run it is confirming on screen while removal runs.
  const [report, setReport] = useState<PruneReport | null>(null);
  const checkButton = useRef<HTMLButtonElement>(null);
  const checking = prune.isPending && prune.variables?.apply === false;
  const removing = prune.isPending && prune.variables?.apply === true;

  function run(apply: boolean) {
    const request = apply
      ? { apply: true as const, imageIds: report?.candidates.map((c) => c.image_id) ?? [] }
      : { apply: false as const };
    prune.mutate(request, { onSuccess: setReport, onSettled: () => setConfirming(false) });
  }

  // The button that opened the dialog is gone once the applied report replaces the dry run, so
  // the dialog cannot hand focus back to it. Land on "Check again" instead of <body>.
  useEffect(() => {
    if (report?.applied) checkButton.current?.focus();
  }, [report]);

  return (
    <div className="space-y-3 text-dense">
      <p className="max-w-[68ch] text-text-secondary">
        Mission images that nothing uses any more: older versions of an updated mission, and images
        of missions you uninstalled. An image used by an install, a run or a pull is never removed.
      </p>

      <Button
        ref={checkButton}
        variant="outline"
        size="sm"
        onClick={() => run(false)}
        disabled={prune.isPending}
        aria-busy={checking}
      >
        {checking ? (
          <Loader2 className="size-3.5 motion-safe:animate-spin" />
        ) : (
          <ScanSearch className="size-3.5" />
        )}
        {report ? "Check again" : "Check for unused images"}
      </Button>

      {prune.isError && (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/[0.06] p-2.5 text-text-secondary"
        >
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
          <span>{errorDetail(prune.error) ?? "Couldn’t check mission images."}</span>
        </p>
      )}

      {/* Always mounted, so the outcome of a removal is announced; the visible report below is
          not a live region because reading the whole table aloud on every check is noise. */}
      <p role="status" className="sr-only">
        {report?.applied && !prune.isError ? appliedSummary(report) : ""}
      </p>

      {report && !prune.isError && (
        <PruneReportView report={report} onRemove={() => setConfirming(true)} />
      )}

      {report && !report.applied && (
        <Dialog
          open={confirming}
          // Once removal has started it cannot be called back, so the dialog stays until it ends:
          // closing it would read as "cancelled" while images are still being removed.
          onClose={() => {
            if (!removing) setConfirming(false);
          }}
          title={`Remove ${plural(report.candidates.length, "unused image", "unused images")}?`}
        >
          <p className="prose-block text-body text-text-secondary">
            Frees up to {formatBytes(report.reclaimable_bytes)}. Only the images listed are removed.
            A removed image is downloaded again only if its mission is pulled again. An image that
            turns out to be in use is left in place and listed.
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirming(false)} disabled={removing}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => run(true)} disabled={prune.isPending}>
              {removing ? "Removing…" : "Remove images"}
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function PruneReportView({
  report,
  onRemove,
}: {
  report: PruneReport;
  onRemove: () => void;
}) {
  const found = report.candidates.length;

  if (report.applied) {
    const kept = keptSentence(report);
    return (
      <div className="space-y-2" data-testid="prune-result">
        <p className="text-foreground">
          {found === 0
            ? "Nothing to remove."
            : `Removed ${report.removed.length} of ${plural(found, "image", "images")}.`}
        </p>
        {kept && <p className="text-text-tertiary">{kept}</p>}
        {report.failed.length > 0 && (
          <div className="space-y-1">
            <p className="text-err">
              {plural(report.failed.length, "image was", "images were")} not removed:
            </p>
            <ul className="space-y-1">
              {report.failed.map((f) => (
                <li key={f.image_id} className="min-w-0 break-words">
                  <span className="font-mono text-foreground">
                    {f.tags[0] ?? f.image_id.slice(0, 19)}
                  </span>
                  <span className="text-text-secondary"> — {f.error}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  if (found === 0) {
    return (
      <p className="text-foreground" data-testid="prune-report">
        No unused mission images. Nothing to reclaim.
      </p>
    );
  }

  return (
    <div className="space-y-3" data-testid="prune-report">
      <p className="text-foreground">
        {plural(found, "unused image", "unused images")} · up to{" "}
        <span className="font-semibold">{formatBytes(report.reclaimable_bytes)}</span> reclaimable
      </p>
      <div className="overflow-x-auto">
        <Table>
          <THead>
            <TR>
              <TH>Mission</TH>
              <TH>Image</TH>
              <TH>Why</TH>
              <TH className="text-right">Size</TH>
            </TR>
          </THead>
          <TBody>
            {report.candidates.map((c) => (
              <TR key={c.image_id}>
                <TD>{c.slug}</TD>
                <TD className="max-w-64 truncate font-mono" title={c.tags.join(", ")}>
                  {c.tags[0] ?? c.image_id.slice(0, 19)}
                </TD>
                <TD>{REASON_LABEL[c.reason] ?? c.reason}</TD>
                <TD className="text-right tabular-nums">{formatBytes(c.size_bytes)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </div>
      <p className="text-caption text-text-tertiary">
        The total is an upper bound: images built on the same base share layers.
      </p>
      <Button variant="destructive" size="sm" onClick={onRemove}>
        <Trash2 className="size-3.5" />
        Remove {plural(found, "image", "images")}…
      </Button>
    </div>
  );
}
