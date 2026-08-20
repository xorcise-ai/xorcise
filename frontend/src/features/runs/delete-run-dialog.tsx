"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { errorDetail } from "@/lib/api/client";
import { useDeleteRun } from "./queries";

/** Confirm-and-delete modal for a single run's result + record.
 *  Controlled (open/onClose) so both the results-page button and the Run
 *  History card's delete icon can drive it. `onDeleted` fires on success. A refusal is surfaced
 *  inline in the SERVER's own words — it names the run's actual state (a `created` run has not
 *  started, so the dialog's old hardcoded "this run is still active" described nothing that was
 *  happening) and the action that unblocks the delete. */
export function DeleteRunDialog({
  runId,
  open,
  onClose,
  onDeleted,
}: {
  runId: string;
  open: boolean;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const del = useDeleteRun();

  async function confirm() {
    try {
      await del.mutateAsync(runId);
    } catch {
      return; // surfaced inline via del.error below
    }
    onClose();
    onDeleted();
  }

  const detail = errorDetail(del.error);

  return (
    <Dialog open={open} onClose={onClose} title="Delete this run?">
      <p className="prose-block text-body text-text-secondary">
        This permanently deletes the run&apos;s <strong>result and record</strong>. It will be
        removed from the runs list, this agent&apos;s history, and the results view. This
        can&apos;t be undone.
      </p>
      {del.isError && (
        <p className="mt-2 text-body text-err">{detail ?? "Delete failed."}</p>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          onClick={confirm}
          disabled={del.isPending}
        >
          {del.isPending ? "Deleting…" : "Delete permanently"}
        </Button>
      </div>
    </Dialog>
  );
}

/** Full-width delete button + confirm used on the results page footer (the
 *  results page routes back to the runs list via `onDeleted`). */
export function DeleteRunButton({
  runId,
  onDeleted,
}: {
  runId: string;
  onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <Trash2 className="size-4" />
        Delete run
      </Button>
      <DeleteRunDialog
        runId={runId}
        open={open}
        onClose={() => setOpen(false)}
        onDeleted={onDeleted}
      />
    </>
  );
}
