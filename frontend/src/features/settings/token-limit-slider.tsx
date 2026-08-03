"use client";

import { Slider } from "@/components/ui/slider";

// Transcript limits span small-context models (~16k) to large-context ones (1M, e.g. Claude 1M).
const MIN = 16_000;
const MAX = 1_000_000;
const STEP = 8_000;

/** Compact "256k" / "1M" for the range labels. */
function fmtK(n: number): string {
  if (n >= 1_000_000) return n % 1_000_000 === 0 ? `${n / 1_000_000}M` : `${(n / 1_000_000).toFixed(1)}M`;
  return `${Math.round(n / 1000)}k`;
}

/**
 * Transcript token-limit picker — a slider with a live numeric readout, replacing the
 * bare number field so the operator can dial the limit up/down instead of typing. `value` is the
 * effective limit and `onChange` reports the new value; the caller owns the "changed vs saved"
 * state. The formatted number is its own node so it reads cleanly and stays testable.
 */
export function TokenLimitSlider({
  value,
  onChange,
  disabled = false,
}: {
  value: number;
  onChange: (tokens: number) => void;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-row font-medium tabular-nums text-heading">
          {value.toLocaleString()}
        </span>
        <span className="text-caption text-text-tertiary">
          tokens · {fmtK(MIN)}–{fmtK(MAX)}
        </span>
      </div>
      <Slider
        ariaLabel="Transcript token limit"
        min={MIN}
        max={MAX}
        step={STEP}
        value={Math.min(MAX, Math.max(MIN, value))}
        disabled={disabled}
        onChange={onChange}
      />
    </div>
  );
}
