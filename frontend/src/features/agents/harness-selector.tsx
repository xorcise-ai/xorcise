"use client";

import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { cn } from "@/components/ui/cn";
import { Input } from "@/components/ui/input";
import { CUSTOM_LABEL, CustomMark, HARNESSES } from "./harnesses";

const CUSTOM_BLURB = "Any CLI agent — replays via the generic adapter.";

/** Vertical stack of selectable harness cards for the agent harness. `value` is the chosen
 * kind (a built-in slug, a custom free-text kind, or "" = generic). Data-driven from HARNESSES.
 * Selection ring uses the amber `border-primary` token — per the design spec ("amber
 * selection ring = the single allowed amber use"), amber here marks SELECTION, which the
 * repo's amber rule explicitly permits (selection-only). */
export function HarnessSelector({
  value,
  onChange,
  compact = false,
}: {
  value: string;
  onChange: (kind: string) => void;
  /** Keeps the desktop three-column registration flow within a laptop-height viewport. */
  compact?: boolean;
}) {
  const isBuiltIn = HARNESSES.some((h) => h.kind === value);
  const [customMode, setCustomMode] = useState<boolean>(value !== "" && !isBuiltIn);

  // Self-healing: `customMode`'s initializer only runs at MOUNT, so a `value` that arrives
  // non-built-in and non-empty on a LATER render (not this component's own first render) would
  // otherwise leave the Custom card unselected and the kind input hidden even though `value`
  // names a custom kind. The primary fix for the known trigger (edit mode seeding) is at the
  // caller — see register-agent-page.tsx's key-based remount — but this effect makes the
  // component correct on its own regardless of how it gets there.
  useEffect(() => {
    if (value !== "" && !isBuiltIn) setCustomMode(true);
    if (isBuiltIn) setCustomMode(false);
  }, [value, isBuiltIn]);

  const card = (selected: boolean) =>
    cn(
      // rounded-md, not rounded-lg: the shape scale gives rounded-lg to chips and rounded-md
      // to selection controls. These tiles are `role="radio"` buttons, and the sibling
      // selection group (register-agent-page's LaunchModeEditor) already uses rounded-md.
      "flex w-full items-start gap-3 rounded-md border p-3 text-left transition-colors",
      compact && "xl:gap-2 xl:p-2.5",
      selected
        ? "border-primary bg-raised text-heading [&_svg]:text-heading"
        // border-border-hover is the app's one hover edge (see globals.css); the old
        // hover:border-text-tertiary borrowed a TYPE token for a border role.
        : "border-border text-text-secondary hover:text-heading hover:border-border-hover",
    );

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Agent harness">
        {HARNESSES.map((h) => {
          const selected = !customMode && value === h.kind;
          return (
            <button
              key={h.kind}
              type="button"
              role="radio"
              aria-checked={selected}
              data-selected={selected}
              onClick={() => {
                setCustomMode(false);
                onChange(h.kind);
              }}
              className={card(selected)}
            >
              <h.Logo />
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="flex items-center gap-2 text-body">
                  {h.name}
                  {!h.live && <span className="text-caption text-text-tertiary">(generic)</span>}
                  {selected && <Check className="size-3.5 shrink-0" aria-hidden />}
                </span>
                <span className={cn("text-dense text-text-tertiary", compact && "xl:hidden")}>
                  {h.blurb}
                </span>
              </span>
            </button>
          );
        })}
        <button
          type="button"
          role="radio"
          aria-checked={customMode}
          data-selected={customMode}
          onClick={() => {
            setCustomMode(true);
            onChange("");
          }}
          className={card(customMode)}
        >
          <CustomMark />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="flex items-center gap-2 text-body">
              {CUSTOM_LABEL}
              {customMode && <Check className="size-3.5 shrink-0" aria-hidden />}
            </span>
            <span className={cn("text-dense text-text-tertiary", compact && "xl:hidden")}>
              {CUSTOM_BLURB}
            </span>
          </span>
        </button>
      </div>
      <label className="block">
        <span className="mb-1 block text-label uppercase text-text-tertiary">Harness ID</span>
        <Input
          type="text"
          value={value}
          onChange={(e) => {
            if (customMode) onChange(e.target.value);
          }}
          placeholder={customMode ? "e.g. my-agent-cli" : undefined}
          readOnly={!customMode}
          aria-readonly={!customMode}
          className={cn(
            !customMode &&
              "cursor-default border-border bg-background text-text-tertiary focus-visible:border-border focus-visible:ring-0",
          )}
        />
      </label>
      <p className={cn("max-w-[68ch] text-dense text-text-tertiary", compact && "xl:text-caption")}>
        {customMode
          ? "Enter the trace source identifier. Its export capabilities remain unknown until a verified adapter is added."
          : "The selected harness uses its verified replay adapter."}
      </p>
    </div>
  );
}
