import { Check, PackagePlus, ShieldCheck } from "lucide-react";
import { ComingSoonPanel } from "@/components/ui/coming-soon";

/**
 * The one description of bundle ingestion, for every surface that has to explain it.
 *
 * Two places make the same promise about the same unshipped capability: the dialog behind
 * "Ingest a bundle", and the Your Own tab when it is empty. Written out twice they would
 * eventually disagree about what ingestion does — which is the failure mode this repo has
 * already hit with `StatusDot` and with the fused-image buildspec. One component, two
 * hosts, and the copy can only change in one place.
 *
 * `align` is the only thing the hosts differ on: the dialog reads left in a column of
 * other left-aligned content, the empty state IS its pane and centres.
 */
export function IngestComingSoon({ align }: { align?: "start" | "center" }) {
  return (
    <ComingSoonPanel
      align={align}
      headline="Bring your own missions."
      steps={[
        { icon: <PackagePlus />, label: "Choose bundle" },
        { icon: <ShieldCheck />, label: "Validate safely" },
        { icon: <Check />, label: "Ready to run" },
      ]}
    >
      Drop in a mission bundle and XORCISE will inspect it, validate it, and add it to
      your local catalog—ready to run.
    </ComingSoonPanel>
  );
}
