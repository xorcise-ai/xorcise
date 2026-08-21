"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/components/ui/cn";
import { toggleFacetValue } from "./filter-missions";

/**
 * Compact multi-select for one catalog facet.
 *
 * Why a popover and not an inline chip row: there are ~15 specialties and their labels are
 * long ("Vulnerability Assessment"), so inline chips cost 3–4 wrapped rows at 1680px and
 * 6 at ~1100px — the filter chrome would push the cards it controls off the pane. The
 * *options* therefore live behind a `h-8` trigger, while the *selection* is echoed as
 * removable chips in the bar itself (see FilterBar), so what is currently narrowing the
 * catalog is always visible without opening anything.
 *
 * Selecting nothing means "all" — there is no explicit All option to get out of sync.
 */
export function FacetSelect({
  label,
  options,
  selected,
  onChange,
  format = (v) => v,
  counts,
}: {
  label: string;
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  /** Display formatter for a raw wire value (e.g. titleCase). */
  format?: (value: string) => string;
  /** Optional per-value catalog counts, rendered after each option ("Web Exploitation (2)"). */
  counts?: Record<string, number>;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const active = selected.length > 0;

  // Dismiss on outside press / Escape. `mousedown` rather than `click` so the panel is
  // gone before the press lands on whatever sits underneath it.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // A facet the catalog never populates is not a control, it's noise.
  if (options.length === 0) return null;

  return (
    <div ref={wrap} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex h-8 items-center gap-1.5 rounded-md border px-2 text-dense transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          active
            ? "border-primary/40 bg-primary/10 text-primary"
            : "border-border bg-card text-foreground hover:border-primary/30",
        )}
      >
        {label}
        {active && <span className="font-semibold">· {selected.length}</span>}
        <ChevronDown
          className={cn("size-3.5", active ? "text-primary" : "text-text-secondary")}
          aria-hidden
        />
      </button>

      {open && (
        <div
          id={panelId}
          role="group"
          aria-label={label}
          className="absolute left-0 top-full z-30 mt-1 max-h-72 w-56 max-w-[80vw] overflow-y-auto rounded-md border border-border bg-card p-1"
        >
          {active && (
            <button
              type="button"
              onClick={() => onChange([])}
              className="mb-1 w-full rounded-md px-2 py-1 text-left text-label uppercase text-text-secondary transition-colors hover:bg-raised hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Clear {label.toLowerCase()}
            </button>
          )}
          {options.map((o) => {
            const on = selected.includes(o);
            return (
              // An option is a real <input type="checkbox"> now — the design system's
              // Checkbox primitive — rather than a button wearing role="checkbox" over a
              // hand-drawn 14px box. Selection is unchanged: the toggle still runs
              // through toggleFacetValue, so the primitive's `next` is redundant here.
              // Selected state is carried by the amber-filled box, which is why the row
              // no longer recolours its own label.
              <Checkbox
                key={o}
                checked={on}
                onChange={() => onChange(toggleFacetValue(selected, o))}
                // The label IS the row, so its content span has to be allowed to shrink —
                // otherwise a long option ("Vulnerability Assessment") sets the row's
                // min-content width and this 224px panel scrolls sideways.
                className="flex w-full gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-raised [&>span:last-child]:min-w-0 [&>span:last-child]:flex-1"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="min-w-0 flex-1 truncate">{format(o)}</span>
                  {counts?.[o] != null && (
                    <span
                      aria-hidden
                      className="shrink-0 text-caption tabular-nums text-text-tertiary"
                    >
                      ({counts[o]})
                    </span>
                  )}
                </span>
              </Checkbox>
            );
          })}
        </div>
      )}
    </div>
  );
}
