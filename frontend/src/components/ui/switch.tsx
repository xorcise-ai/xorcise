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
        "relative inline-flex h-[18px] w-[34px] shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-raised",
      )}
    >
      <span
        className={cn(
          "inline-block size-[14px] transform rounded-full shadow transition-transform",
          checked ? "translate-x-[18px] bg-[#160c00]" : "translate-x-0.5 bg-[#eee]",
        )}
      />
    </button>
  );
}
