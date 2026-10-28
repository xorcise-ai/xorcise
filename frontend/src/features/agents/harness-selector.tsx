"use client";

import { useState } from "react";
import { AlertTriangle, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";
import { Input } from "@/components/ui/input";
import { GENERIC_KIND, GENERIC_LABEL, GenericMark, HARNESSES } from "./harnesses";
import { useHarnessDescriptors } from "./use-harnesses";

const GENERIC_BLURB =
  "Any other CLI agent — replays via the generic adapter unless its trace names a harness.";

/** Characters `a` and `b` share, by Ratcliff/Obershelp: the longest common block, then the
 *  same on each side of it. The measure behind Python's difflib, so the suggestion here matches
 *  the one the server and CLI print for the same typo. */
function matchingChars(a: string, b: string): number {
  let size = 0;
  let ai = 0;
  let bi = 0;
  for (let i = 0; i < a.length; i++) {
    for (let j = 0; j < b.length; j++) {
      let k = 0;
      while (i + k < a.length && j + k < b.length && a[i + k] === b[j + k]) k++;
      if (k > size) [size, ai, bi] = [k, i, j];
    }
  }
  if (size === 0) return 0;
  return (
    size +
    matchingChars(a.slice(0, ai), b.slice(0, bi)) +
    matchingChars(a.slice(ai + size), b.slice(bi + size))
  );
}

/** The known kind closest to `kind` (difflib's get_close_matches, n=1, cutoff 0.6), or null. */
function closestKind(kind: string, known: readonly string[]): string | null {
  const typed = kind.toLowerCase();
  let best: string | null = null;
  let bestRatio = 0.6;
  for (const candidate of known) {
    // difflib's argument order (seq1 = candidate, seq2 = typed): ties break the same way.
    const ratio = (2 * matchingChars(candidate, typed)) / (typed.length + candidate.length);
    if (ratio >= bestRatio) [best, bestRatio] = [candidate, ratio];
  }
  return best;
}

/** Vertical stack of selectable harness cards for the agent harness. `value` is the chosen kind:
 * a built-in slug, `generic` or your own label under Generic ("" is a cleared Generic ID, which
 * saves as generic), or null while no harness has been chosen. Data-driven from HARNESSES.
 * Selection ring uses the amber `border-primary` token — per the design spec ("amber
 * selection ring = the single allowed amber use"), amber here marks SELECTION, which the
 * repo's amber rule explicitly permits (selection-only). */
export function HarnessSelector({
  value,
  onChange,
  compact = false,
}: {
  value: string | null;
  onChange: (kind: string) => void;
  /** Keeps the desktop three-column registration flow within a laptop-height viewport. */
  compact?: boolean;
}) {
  // Judged as the server will store it: trimmed, so a pasted " openhands" is OpenHands.
  const typedKind = value?.trim() ?? "";
  const isBuiltIn = value !== null && HARNESSES.some((h) => h.kind === typedKind);
  // One card covers "no specific harness" and "my own CLI": any kind that is not a built-in
  // replays with the generic adapter unless its trace identifies a real one. Derived from
  // `value` alone, so it is right on any render however the value arrived (an edit seeding a
  // saved label, a "Use OpenHands" click, a built-in typed out in full).
  const genericMode = value !== null && !isBuiltIn;
  // Typing a label can pass through a built-in slug (`codex` on the way to `codex-fork`). The
  // card follows what would be saved, but the field stays editable until it is left; only then
  // does a built-in settle into its read-only card.
  const [typing, setTyping] = useState(false);
  const idEditable = genericMode || typing;
  // The registered kinds come from the server, the same list the CLI gate checks. Until it loads
  // (or if it cannot) there is nothing to judge against, so nothing is flagged.
  const { descriptors } = useHarnessDescriptors();
  const ownLabel = genericMode && typedKind !== "" && typedKind !== GENERIC_KIND;
  const knownKinds = descriptors?.map((d) => d.kind) ?? [];
  const unrecognised = ownLabel && knownKinds.length > 0 && !knownKinds.includes(typedKind);
  const suggestion = unrecognised ? closestKind(typedKind, knownKinds) : null;
  const suggestionName = HARNESSES.find((h) => h.kind === suggestion)?.name ?? suggestion;
  // `generic` itself, or a blank ID (which saves as generic): no verified adapter either. Its
  // runs replay with the generic adapter, a best effort, so it gets the same warning box as a
  // label of your own rather than a quiet line of help.
  const genericUnverified = genericMode && !ownLabel;
  const warn = unrecognised || genericUnverified;

  const help =
    value === null
      ? "Choose a harness. Pick Generic for any other CLI agent."
      : isBuiltIn
        ? "The selected harness uses its verified replay adapter."
        : "Your own label. Its runs may replay with the generic adapter, which is not verified against any harness.";

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
          const selected = typedKind === h.kind;
          return (
            <button
              key={h.kind}
              type="button"
              role="radio"
              aria-checked={selected}
              data-selected={selected}
              onClick={() => {
                setTyping(false);
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
          aria-checked={genericMode}
          data-selected={genericMode}
          onClick={() => {
            // Choosing it fills the ID with `generic`; clicking it again keeps a typed label.
            if (!genericMode) onChange(GENERIC_KIND);
          }}
          className={card(genericMode)}
        >
          <GenericMark />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="flex items-center gap-2 text-body">
              {GENERIC_LABEL}
              {genericMode && <Check className="size-3.5 shrink-0" aria-hidden />}
            </span>
            <span className={cn("text-dense text-text-tertiary", compact && "xl:hidden")}>
              {GENERIC_BLURB}
            </span>
          </span>
        </button>
      </div>
      <label className="block">
        <span className="mb-1 block text-label uppercase text-text-tertiary">Harness ID</span>
        <Input
          type="text"
          value={value ?? ""}
          onChange={(e) => {
            if (!idEditable) return;
            setTyping(true);
            onChange(e.target.value);
          }}
          onBlur={() => setTyping(false)}
          placeholder={genericMode ? GENERIC_KIND : undefined}
          readOnly={!idEditable}
          aria-readonly={!idEditable}
          // Read-only, it only echoes the chosen card, so it takes no Tab stop and draws no focus
          // ring when clicked.
          tabIndex={idEditable ? undefined : -1}
          className={cn(
            !idEditable &&
              "cursor-default border-border bg-background text-text-tertiary focus-visible:border-border focus-visible:ring-0 focus-visible:outline-none",
          )}
        />
      </label>
      {/* Always mounted, and holding only the message: screen readers skip a live region that
          arrives already filled, and anything inside one (a button's label too) is read out as
          part of the announcement. The box is styled only while it has something to say. */}
      <div
        className={cn(
          warn &&
            "flex items-start gap-2 rounded-md border border-warning/30 bg-warning/[0.06] p-2.5 text-dense text-text-secondary",
        )}
      >
        {warn && (
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
        )}
        <div className="min-w-0 space-y-1.5">
          <div role="status" className="space-y-1.5">
            {unrecognised && (
              <>
                <p>
                  <span className="font-medium text-heading">
                    “{typedKind}” isn’t a registered harness.
                  </span>
                  {suggestion && ` Did you mean ${suggestionName}?`}
                </p>
                <p>
                  Runs may replay with the generic adapter. That is expected for your own CLI;
                  check the spelling if you meant a built-in one.
                </p>
              </>
            )}
            {genericUnverified && (
              <>
                <p>
                  <span className="font-medium text-heading">
                    The generic adapter isn’t verified against any harness.
                  </span>
                </p>
                <p>
                  {typedKind === "" ? "A blank ID saves as generic. " : ""}
                  Runs replay with it as a best effort, so which spans your agent exports is
                  unknown until you look at a run. Replace the ID with your own label if you
                  like, e.g. my-agent-cli.
                </p>
              </>
            )}
          </div>
          {suggestion && (
            <Button type="button" variant="outline" size="sm" onClick={() => onChange(suggestion)}>
              Use {suggestionName}
            </Button>
          )}
        </div>
      </div>
      {!warn && (
        <p className={cn("text-dense text-text-tertiary", compact && "xl:text-caption")}>
          {help}
        </p>
      )}
    </div>
  );
}
