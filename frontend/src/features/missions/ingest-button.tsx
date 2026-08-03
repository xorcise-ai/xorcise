"use client";

import { useState } from "react";
import { Check, PackagePlus, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ComingSoonPanel } from "@/components/ui/coming-soon";
import { Dialog } from "@/components/ui/dialog";

/**
 * "Ingest a bundle" — the GUI counterpart of `xorcise mission ingest`.
 *
 * Bundle ingestion is not part of this release, but the control stays active so the
 * capability is discoverable. For now it opens a short product preview; once ingestion
 * ships, this component can swap the preview for the existing file-browser flow.
 */
export function IngestButton() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <PackagePlus className="size-4" />
        Ingest a bundle
      </Button>

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Bundle ingestion"
      >
        <ComingSoonPanel
          headline="Bring your own missions."
          steps={[
            { icon: <PackagePlus />, label: "Choose bundle" },
            { icon: <ShieldCheck />, label: "Validate safely" },
            { icon: <Check />, label: "Ready to run" },
          ]}
        >
          Drop in a mission bundle and XORCISE will inspect it, validate it, and add
          it to your local catalog—ready to run.
        </ComingSoonPanel>

        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-caption text-text-tertiary">
            Until then, published missions are ready in XORCISE Remote.
          </p>
          <Button
            className="shrink-0"
            onClick={() => setOpen(false)}
          >
            Back to catalog
          </Button>
        </div>
      </Dialog>
    </>
  );
}
