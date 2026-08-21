import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";
import { StatusDot, type DotTone } from "./dot";

export interface ChipProps extends Omit<HTMLAttributes<HTMLDivElement>, "children"> {
  /** The key. Rendered in the label role; do not pre-uppercase it. */
  label: ReactNode;
  /** The value. Rendered in the dense role — this is the part the operator reads. */
  value: ReactNode;
  /** Dot tone. Pass `null` for a chip whose value needs no state colour. */
  tone?: DotTone | null;
  /** Leading icon, in place of the dot — e.g. a harness mark. 14px square. */
  icon?: ReactNode;
}

/**
 * Chip — the connection readout: KEY · dot · value.
 *
 * This is the shape the console uses wherever a named piece of the environment reports
 * what it currently is: the dashboard's System card (Modules / Catalog / Topology), the
 * live-run header (Environment / Harness / Objective), the status bar. All of those were
 * hand-rolled, so the key sat at four different sizes and the dot at three.
 *
 * The label is text-tertiary rather than secondary on purpose: in a row of chips the
 * OPERATOR is scanning values, not keys, so the key has to recede. It still clears AA
 * (#8a8a8a on #1a1a1a is 4.9:1).
 */
export function Chip({
  className,
  label,
  value,
  tone = "ok",
  icon,
  ...props
}: ChipProps) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2",
        className,
      )}
      {...props}
    >
      <span className="text-label uppercase text-text-tertiary">{label}</span>
      <span className="inline-flex items-center gap-1.5">
        {icon
          ? <span className="inline-flex size-3.5 items-center justify-center text-text-secondary">{icon}</span>
          : tone && <StatusDot tone={tone} />}
        <span className="text-dense text-foreground">{value}</span>
      </span>
    </div>
  );
}

/** The row that holds them — 8px gutter, wraps rather than overflows. */
export function ChipRow({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-wrap gap-2", className)} {...props} />;
}
