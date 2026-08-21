"use client";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";

const MIN_MIN = 5;
const MAX_MIN = 90;
const STEP_MIN = 5;
/** One-click budgets for the common cases; the slider still covers the full range. */
const PRESET_MIN = [5, 10, 30, 60];

/** Budget picker: a minutes slider over a seconds value, with a live readout and
 *  quick-pick presets. The run contract is in seconds, so state stays in seconds
 *  and this only presents/edits it in whole minutes. */
export function BudgetSlider({
  seconds,
  onChange,
  disabled = false,
}: {
  seconds: number;
  onChange: (seconds: number) => void;
  disabled?: boolean;
}) {
  const minutes = Math.round(seconds / 60);
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-body text-heading">{minutes} min</span>
        <span className="text-caption text-text-tertiary">
          {seconds}s · range {MIN_MIN}–{MAX_MIN} min
        </span>
      </div>
      <Slider
        ariaLabel="Budget (minutes)"
        min={MIN_MIN}
        max={MAX_MIN}
        step={STEP_MIN}
        value={Math.min(MAX_MIN, Math.max(MIN_MIN, minutes))}
        disabled={disabled}
        onChange={(m) => onChange(m * 60)}
      />
      <div className="flex items-center gap-1.5 pt-0.5">
        {PRESET_MIN.map((m) => (
          <Button
            key={m}
            type="button"
            // The picked preset is the outline variant (amber edge + amber label); the rest
            // are ghost. aria-pressed still carries the state for anyone not reading colour.
            variant={minutes === m ? "outline" : "ghost"}
            size="sm"
            aria-pressed={minutes === m}
            // Starts with the visible "5m" (SC 2.5.3 label-in-name), then spells the unit out.
            aria-label={`${m}m — set a ${m} minute budget`}
            disabled={disabled}
            onClick={() => onChange(m * 60)}
          >
            {m}m
          </Button>
        ))}
      </div>
    </div>
  );
}
