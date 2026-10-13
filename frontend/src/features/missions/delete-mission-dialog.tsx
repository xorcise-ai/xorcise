"use client";

import { useState } from "react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { errorDetail } from "@/lib/api/client";
import type { CatalogEntry, MissionDeleteOut } from "@/lib/api/types";
import { useToastStore, type Toast, type ToastTone } from "@/stores/toasts";
import { hasImage } from "./mission-card";
import { formatBytes, useDeleteMission } from "./queries";

/** What the operator is told about the image after an uninstall that also removed it, worded as
 *  the CLI's `mission delete --purge`. Keyed by the server's `image_outcome`; the uninstall
 *  itself stood in every case. */
const IMAGE_OUTCOME_TOAST: Record<string, { tone: ToastTone; title: string } | undefined> = {
  removed: { tone: "ok", title: "Removed its image" },
  kept: { tone: "info", title: "Kept its image" },
  none: { tone: "info", title: "No image removed" },
  failed: { tone: "warn", title: "Uninstalled, but could not remove its image" },
};

function imageOutcomeToast(out: MissionDeleteOut): Omit<Toast, "id"> {
  const known = IMAGE_OUTCOME_TOAST[out.image_outcome];
  return {
    tone: known?.tone ?? "info",
    title: known?.title ?? `Image ${out.image_outcome}`,
    body: out.detail ?? undefined,
  };
}

/** Confirm-and-uninstall modal for an installed mission. A mission that ships an image also
 *  offers to remove it, unticked every time the dialog opens so a plain confirm keeps the image.
 *  A refusal is surfaced inline in the server's own words and the dialog stays open. */
export function DeleteMissionDialog({
  entry,
  open,
  onClose,
  onDeleted,
}: {
  entry: CatalogEntry;
  open: boolean;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const del = useDeleteMission();
  const pushToast = useToastStore((s) => s.push);
  const [purgeImage, setPurgeImage] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setPurgeImage(false);
  }

  async function confirm() {
    let out: MissionDeleteOut | undefined;
    try {
      out = await del.mutateAsync({ id: entry.mission_id, purgeImage });
    } catch {
      return; // surfaced inline via del.error below
    }
    onClose();
    if (out) pushToast(imageOutcomeToast(out));
    onDeleted();
  }

  const detail = errorDetail(del.error);

  return (
    <Dialog open={open} onClose={onClose} title={`Delete ${entry.name}?`}>
      <p className="prose-block text-body text-text-secondary">
        This uninstalls the mission. Recorded runs and results are kept.
      </p>
      {hasImage(entry) && (
        <div className="mt-4">
          <Checkbox checked={purgeImage} onChange={setPurgeImage} disabled={del.isPending}>
            Also remove its image
            {entry.image_size_bytes != null && (
              <>
                {" "}
                <span className="whitespace-nowrap">
                  ({formatBytes(entry.image_size_bytes)})
                </span>
              </>
            )}
          </Checkbox>
          <p className="mt-1 pl-6 text-dense text-text-tertiary">
            Frees its disk space. Installing it again downloads the image.
          </p>
        </div>
      )}
      {del.isError && (
        <p className="mt-2 text-body text-err">{detail ?? "Delete failed."}</p>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="destructive" onClick={confirm} disabled={del.isPending}>
          {del.isPending ? "Deleting…" : "Delete mission"}
        </Button>
      </div>
    </Dialog>
  );
}
