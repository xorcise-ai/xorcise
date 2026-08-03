import type { ReactElement, ReactNode } from "react";
import { Sparkles } from "lucide-react";
import { cn } from "./cn";

/**
 * The house treatment for a capability XORCISE has committed to but has not shipped.
 *
 * It exists so that "not yet" is a DESIGNED state rather than a disabled control. A greyed-out
 * button says the feature is here and you are not allowed to use it; this panel says the feature
 * is coming and shows you its shape. That distinction matters most where the honest alternative
 * is nothing at all — a hidden capability is undiscoverable, and a stub one misconfigures the
 * install (see the Distributed deployment card, which was withdrawn for exactly that reason).
 *
 * The rule that keeps it honest: this panel renders NO controls. Steps are illustrative chips,
 * not buttons. Anything that can be clicked belongs outside it.
 *
 * ── Why container queries and not breakpoints ──
 * The panel has two hosts of very different widths: a 28rem dialog and a full-width settings
 * card. Laid out stacked in both, the wide one reproduced the §7.1 defect globals.css already
 * documents — prose correctly stopping at its readable measure while the box ran on for another
 * 1400px, so the panel read as three-quarters empty. Viewport breakpoints cannot tell those two
 * hosts apart (both can be on the same 1440px screen), but `@container` can: the panel measures
 * ITSELF and splits into prose + a stacked step list once it is wide enough to strand a line.
 */
export function ComingSoonPanel({
  headline,
  steps,
  className,
  children,
}: {
  headline: string;
  /** Illustrative, not interactive — the shape of the flow. */
  steps: { icon: ReactElement<{ className?: string }>; label: string }[];
  className?: string;
  /** Body prose. */
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "@container relative overflow-hidden rounded-lg border border-primary/20 bg-deepest",
        className,
      )}
    >
      {/* Amber wash from the top-right — the brand's one decorative gesture, and the reason
          the panel reads as a preview rather than as another card. */}
      <div
        aria-hidden
        className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(232,184,75,0.16),transparent_52%)]"
      />
      <div className="relative p-5">
        <div className="mb-5 flex items-center justify-between gap-3">
          <span className="inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary/5 px-2.5 py-1 text-label uppercase text-primary">
            <span className="size-1.5 rounded-full bg-primary shadow-[0_0_8px_rgba(232,184,75,0.7)]" />
            Coming soon
          </span>
          <Sparkles className="size-4 text-primary/70" aria-hidden />
        </div>

        <div className="grid gap-5 @3xl:grid-cols-[1.35fr_1fr] @3xl:items-center @3xl:gap-8">
          <div>
            {/* Measure caps, not layout limits. Narrow: the dialog is the cap. Wide: the
                column is, and prose stops at the 68ch readable band the type rules set. */}
            <h3 className="max-w-sm text-lead text-heading @3xl:max-w-none">{headline}</h3>
            <p className="mt-2 max-w-md text-body text-text-secondary @3xl:max-w-[68ch]">
              {children}
            </p>
          </div>

          {/* Three across while the panel is narrow; a vertical flow once it is wide, where a
              row of two-word chips would each be 400px of mostly nothing. */}
          <div className="grid gap-2 @sm:grid-cols-3 @3xl:grid-cols-1">
            {steps.map((step) => (
              <PreviewStep key={step.label} icon={step.icon} label={step.label} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function PreviewStep({
  icon,
  label,
}: {
  icon: ReactElement<{ className?: string }>;
  label: string;
}) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-card/70 px-3 py-2.5 text-caption text-foreground">
      <span className="text-primary [&>svg]:size-3.5" aria-hidden>
        {icon}
      </span>
      <span>{label}</span>
    </div>
  );
}
