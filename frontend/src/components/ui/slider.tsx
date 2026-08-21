"use client";

import type { CSSProperties } from "react";
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
  // The filled portion of the track is a gradient stop, and CSS cannot read the value —
  // so the percentage is handed to the stylesheet as a custom property. Clamped because a
  // controlled value can briefly sit outside min..max while a caller re-derives its range.
  const pct = max > min ? ((Math.min(max, Math.max(min, value)) - min) / (max - min)) * 100 : 0;

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
      style={{ "--range-pct": `${pct}%` } as CSSProperties}
      className={cn("range-control", className)}
    />
  );
}
