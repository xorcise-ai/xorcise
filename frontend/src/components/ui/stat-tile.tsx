import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";

/* The value's tone. `primary` is the ONE number in a row that the page is actually about
   — the Figma board tints only "Average" — and `muted` demotes a figure that is context
   rather than headline. The status tones exist because a tile can carry a verdict; that
   stays inside §12's rule because the tile's own label names the thing being coloured. */
const valueVariants = cva("tabular-nums", {
  variants: {
    tone: {
      /* text/foreground, not text/heading. The tiles are the brightest thing on a card
         only because they are the biggest; taking the heading value as well made a row of
         five figures compete with the page head above them. Figma sets #d4d4d4. */
      default: "text-foreground",
      primary: "text-primary",
      muted: "text-text-secondary",
      ok: "text-ok",
      err: "text-err",
      warn: "text-warning",
    },
    /* Longer values — a timestamp, a model id — render a rung down so they do not wrap.
       `row` rather than `body`: a stat value is by definition a single line, which is the
       exact case the row role exists for. */
    size: {
      default: "text-stat",
      /* A hero tile — the ONE figure a whole page is about, in a cell wide enough to
         carry it. Same component, same eyebrow, one rung up the declared scale, so a
         headline count never has to reach for a raw text-3xl again. */
      display: "text-display",
      small: "text-row font-semibold",
    },
  },
  defaultVariants: { tone: "default", size: "default" },
});

export interface StatTileProps
  extends Omit<HTMLAttributes<HTMLDivElement>, "children">,
    VariantProps<typeof valueVariants> {
  /** The eyebrow. Rendered in the label role, which uppercases it — pass normal case. */
  label: ReactNode;
  /** The figure. A short string or number — never a sentence. */
  value: ReactNode;
  /** Longer values render a size down so they don't wrap. */
  small?: boolean;
}

/**
 * StatTile — a label eyebrow over a single glanceable figure.
 *
 * Promoted here from `features/results/performance-summary`, where it had been living as a
 * feature-local export that four unrelated surfaces (the dashboard fleet strip, the Results
 * agent cards, the Agents list, the Agent-detail header) all reached across the app to
 * import. It is an atom of the design system — the Figma component board draws it beside
 * Button and Badge — so it belongs with the other atoms, and its value now comes from the
 * declared `stat` rung instead of a raw `text-lg`.
 *
 * Renders `<dt>`/`<dd>` inside a wrapping div, so a row of tiles is a real description
 * list. Use {@link StatTileRow}, or any `<dl>`, as the parent.
 */
export function StatTile({
  className,
  label,
  value,
  tone,
  size,
  small = false,
  ...props
}: StatTileProps) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)} {...props}>
      <dt className="truncate text-label uppercase text-text-tertiary">
        {label}
      </dt>
      <dd className={cn(valueVariants({ tone, size: small ? "small" : size }))}>
        {value}
      </dd>
    </div>
  );
}

/**
 * The row that holds them. A real `<dl>`, with the 16px gutter declared once rather than at
 * every call site, and wrapping rather than overflowing on a narrow viewport.
 */
export function StatTileRow({
  className,
  ...props
}: HTMLAttributes<HTMLDListElement>) {
  return <dl className={cn("flex flex-wrap gap-4", className)} {...props} />;
}

export type StatTone = NonNullable<VariantProps<typeof valueVariants>["tone"]>;
export { valueVariants as statValueVariants };
