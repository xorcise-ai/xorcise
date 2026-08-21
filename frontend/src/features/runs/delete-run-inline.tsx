"use client";

import { useEffect, useRef } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { errorDetail } from "@/lib/api/client";
import { useDeleteRun } from "./queries";

/**
 * Delete confirmation that happens INSIDE the run's own card.
 *
 * The page-level modal was the wrong shape for this action: it tore the operator out of the
 * grid, floated a box with no visual tie to the run being deleted, and — in a scrolled list —
 * could land clipped over unrelated cards, so the question "delete which run?" was answered by
 * a run id nobody reads. Confirming in place keeps the answer where the question was asked:
 * the card blurs behind the prompt, so the thing about to be destroyed is literally the
 * backdrop of the confirmation.
 *
 * The blur is `backdrop-filter` over a translucent surface — it blurs the CARD, not this panel,
 * which is why the panel's own text stays crisp.
 */
export function DeleteRunInline({
  runId,
  runName,
  onCancel,
  onDeleted,
}: {
  runId: string;
  runName: string;
  onCancel: () => void;
  onDeleted: () => void;
}) {
  const del = useDeleteRun();
  const cancelRef = useRef<HTMLButtonElement>(null);

  // Focus lands on Cancel, not Delete: this overlay appears over the card the pointer is
  // already on, and a focused destructive button one Enter away from an irreversible delete is
  // the wrong default. Escape backs out, matching every other dismissible surface here.
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  async function confirm() {
    try {
      await del.mutateAsync(runId);
    } catch {
      return; // the server's refusal is rendered inline below
    }
    onDeleted();
  }

  const detail = errorDetail(del.error);

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-label={`Delete run ${runName}?`}
      // Clicks must not reach the card's stretched link underneath.
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
      }}
      className="animate-scale-in absolute inset-0 z-30 flex flex-col justify-center gap-2 rounded-xl border border-err/40 bg-card/75 p-3 backdrop-blur-sm"
    >
      <p className="flex items-center gap-1.5 text-body text-heading">
        <AlertTriangle className="size-3.5 shrink-0 text-err" aria-hidden />
        Delete this run?
      </p>
      <p className="text-caption text-text-secondary">
        Permanently removes its <strong className="text-foreground">result and record</strong>.
        This can&apos;t be undone.
      </p>
      {del.isError && (
        <p role="alert" className="text-caption text-err">
          {detail ?? "Delete failed."}
        </p>
      )}
      <div className="mt-1 flex items-center gap-2">
        <Button
          variant="destructive"
          size="sm"
          onClick={confirm}
          disabled={del.isPending}
          className="h-7 px-2.5"
        >
          {del.isPending ? "Deleting…" : "Delete"}
        </Button>
        <Button
          ref={cancelRef}
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={del.isPending}
          className="h-7 px-2.5"
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
