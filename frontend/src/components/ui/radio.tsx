"use client";

import type { ReactNode } from "react";
import { cn } from "./cn";

export interface RadioProps {
  /** Groups the buttons — required for arrow-key navigation between them. */
  name: string;
  /** This button's value. */
  value: string;
  checked: boolean;
  onChange: (value: string) => void;
  disabled?: boolean;
  /** Visible label. When omitted, pass `aria-label` via `label` for the a11y name. */
  children?: ReactNode;
  /** Accessible name when there is no visible label. */
  label?: string;
  className?: string;
}

/**
 * Radio — 16px, ring-only when unselected, amber core when selected.
 *
 * Built on a real `<input type="radio">` kept in `sr-only` rather than a `<button
 * role="radio">`, because the roving-focus and arrow-key behaviour of a radio group is
 * something the platform already implements correctly and a hand-rolled version almost
 * never does. The visible control is a sibling span driven by `peer-checked` /
 * `peer-focus-visible`, so the styling is declarative and the semantics are free.
 *
 * The unselected ring is border-input, not border-border: this is a control, so WCAG
 * 1.4.11's 3:1 applies to the edge that identifies it. border-border is decorative and
 * sits far below that floor by design.
 */
export function Radio({
  name,
  value,
  checked,
  onChange,
  disabled = false,
  children,
  label,
  className,
}: RadioProps) {
  return (
    <label
      className={cn(
        "group inline-flex items-center gap-2",
        disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
        className,
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        aria-label={children ? undefined : label}
        onChange={() => onChange(value)}
        className="peer sr-only"
      />
      <span
        aria-hidden
        className="inline-flex size-4 shrink-0 items-center justify-center rounded-full border border-input bg-deepest transition-colors peer-checked:border-primary peer-focus-visible:outline-none peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background"
      >
        {/* group-has, not peer-checked: `peer-*` compiles to `.peer:checked ~ &`, a SIBLING
            combinator, and this core is a grandchild of the input rather than a sibling —
            so peer-checked would silently never match and the selected state would render
            as an empty ring. The label is the `group`, and it does contain the input. */}
        <span className="size-2 scale-0 rounded-full bg-primary transition-transform duration-[var(--dur-fast)] group-has-[:checked]:scale-100" />
      </span>
      {children && <span className="text-dense text-foreground">{children}</span>}
    </label>
  );
}

/** The group wrapper — gives the buttons a shared `role` and a consistent gutter. */
export function RadioGroup({
  className,
  label,
  children,
}: {
  className?: string;
  label?: string;
  children?: ReactNode;
}) {
  return (
    <div role="radiogroup" aria-label={label} className={cn("flex flex-col gap-2", className)}>
      {children}
    </div>
  );
}
