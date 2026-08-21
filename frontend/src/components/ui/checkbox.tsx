"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { cn } from "./cn";

export interface CheckboxProps {
  /** `true` | `false` | `"mixed"` — mixed is the parent of a partly-selected set. */
  checked: boolean | "mixed";
  onChange: (next: boolean) => void;
  disabled?: boolean;
  children?: ReactNode;
  /** Accessible name when there is no visible label. */
  label?: string;
  className?: string;
}

/**
 * Checkbox — 16px, 2px radius, amber fill when set.
 *
 * `mixed` drives the native `indeterminate` property, which cannot be expressed as an
 * attribute and so has to be assigned to the DOM node — that is what the effect below is
 * for. Setting only `aria-checked="mixed"` would announce correctly and still paint the
 * platform's checked tick, which is the wrong picture.
 *
 * The tick and the mixed bar are both --color-on-primary, the same ink the Button and the
 * Switch knob use on the same amber fill, so every amber-on-dark knockout in the console
 * is one value.
 */
export function Checkbox({
  checked,
  onChange,
  disabled = false,
  children,
  label,
  className,
}: CheckboxProps) {
  const ref = useRef<HTMLInputElement>(null);
  const mixed = checked === "mixed";

  useEffect(() => {
    if (ref.current) ref.current.indeterminate = mixed;
  }, [mixed]);

  return (
    <label
      className={cn(
        "group inline-flex items-center gap-2",
        disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
        className,
      )}
    >
      <input
        ref={ref}
        type="checkbox"
        checked={checked === true}
        disabled={disabled}
        aria-checked={mixed ? "mixed" : checked === true}
        aria-label={children ? undefined : label}
        onChange={(e) => onChange(e.target.checked)}
        className="peer sr-only"
      />
      <span
        aria-hidden
        className={cn(
          "inline-flex size-4 shrink-0 items-center justify-center rounded-xs border transition-colors peer-focus-visible:outline-none peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background",
          checked === false
            ? "border-input bg-deepest"
            : "border-primary bg-primary",
        )}
      >
        {checked === true && (
          <svg viewBox="0 0 10 8" className="size-2.5 text-on-primary" fill="none">
            <path
              d="M1 4.2 3.6 6.8 9 1.4"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="square"
            />
          </svg>
        )}
        {mixed && <span className="h-0.5 w-2 rounded-full bg-on-primary" />}
      </span>
      {children && <span className="text-dense text-foreground">{children}</span>}
    </label>
  );
}
