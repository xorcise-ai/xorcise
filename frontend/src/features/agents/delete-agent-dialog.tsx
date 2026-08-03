"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useDeleteAgent } from "./queries";

/** Delete an agent after a confirm that spells out the runs+results cascade. `onDeleted`
 *  fires on success (the detail page routes back to the list). */
export function DeleteAgentButton({
  name,
  onDeleted,
}: {
  name: string;
  onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const del = useDeleteAgent();

  async function confirm() {
    await del.mutateAsync(name);
    setOpen(false);
    onDeleted();
  }

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <Trash2 className="size-4" />
        Delete agent
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} title={`Delete “${name}”?`}>
        <p className="max-w-[68ch] text-body text-text-secondary">
          This permanently deletes the agent <strong>and all its runs and results</strong>. They
          do not outlive the agent. This can&apos;t be undone.
        </p>
        {del.isError && <p className="mt-2 text-body text-err">Delete failed.</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={confirm} disabled={del.isPending}>
            {del.isPending ? "Deleting…" : "Delete permanently"}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
