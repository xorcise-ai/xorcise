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
      className={cn(
        "h-1.5 w-full cursor-pointer appearance-none rounded-full bg-border accent-primary disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
    />
  );
}
