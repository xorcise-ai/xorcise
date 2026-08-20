"use client";

import { cn } from "./cn";

/**
 * A minimal accessible toggle (role="switch"). Controlled: `checked` + `onChange`.
 * Track turns primary when on; the thumb slides. Keyboard-operable as a button.
 */
export function Switch({
  checked,
  onChange,
  disabled = false,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        // The track is 18px because that is the drawn control, but 18 is under WCAG 2.5.8's
        // 24px pointer floor. A transparent ::before centred on the track lifts the HIT AREA
        // to 24px without moving anything: the pseudo-element belongs to the button, so the
        // extra 3px above and below are clickable while the switch still reads as 18px.
        "relative inline-flex h-[18px] w-[34px] shrink-0 items-center rounded-full transition-colors duration-[var(--dur-fast)] before:absolute before:inset-x-0 before:top-1/2 before:h-6 before:-translate-y-1/2 before:content-[''] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-raised",
      )}
    >
      <span
        className={cn(
          "inline-block size-[14px] transform rounded-full shadow transition-transform duration-[var(--dur-fast)]",
          checked ? "translate-x-[18px] bg-on-primary" : "translate-x-0.5 bg-knob",
        )}
      />
    </button>
  );
}
