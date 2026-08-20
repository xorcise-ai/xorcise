"use client";

import { cn } from "./cn";

/** A themed range input. Reports the parsed numeric value via `onChange`. */
export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
  ariaLabel,
  disabled = false,
  className,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
  ariaLabel: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <input
      type="range"
      aria-label={ariaLabel}
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
      // .range-control (globals.css) keeps the 6px track but makes the CONTROL 24px tall,
      // which is WCAG 2.5.8's pointer-target floor. The track is a pseudo-element, so the
      // extra height is hit area only — nothing moves.
      className={cn("range-control", className)}
    />
  );
}
