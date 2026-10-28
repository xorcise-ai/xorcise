"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { errorDetail, isForceableRefusal } from "@/lib/api/client";
import { useDeleteAgent } from "./queries";

/** Delete an agent after a confirm that spells out the runs+results cascade. `onDeleted`
 *  fires on success (the detail page routes back to the list). A refusal is shown in the
 *  server's own words, which name the runs in the way and what unblocks them: terminate a run
 *  that is still running, wait for one being graded, or, when a finished run's environment
 *  could not be torn down, "Delete anyway" (#173). */
export function DeleteAgentButton({
  name,
  onDeleted,
}: {
  name: string;
  onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const del = useDeleteAgent();

  async function confirm(force = false) {
    try {
      await del.mutateAsync({ name, force });
    } catch {
      return; // the server's refusal is rendered inline below
    }
    setOpen(false);
    onDeleted();
  }

  const detail = errorDetail(del.error);
  const forceable = isForceableRefusal(del.error);

  // Forget the last refusal on close, so reopening never shows a stale one with "Delete anyway".
  function close() {
    del.reset();
    setOpen(false);
  }

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <Trash2 className="size-4" />
        Delete agent
      </Button>
      <Dialog open={open} onClose={close} title={`Delete “${name}”?`}>
        <p className="text-body text-text-secondary">
          This permanently deletes the agent <strong>and all its runs and results</strong>. They
          do not outlive the agent. This can&apos;t be undone.
        </p>
        {del.isError && (
          <p role="alert" className="mt-2 text-body text-err">
            {detail ?? "Delete failed."}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => confirm(forceable)}
            disabled={del.isPending}
          >
            {del.isPending ? "Deleting…" : forceable ? "Delete anyway" : "Delete permanently"}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
