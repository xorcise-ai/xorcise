import { describe, it, expect } from "vitest";
import { cn } from "./cn";

describe("cn", () => {
  it("merges and dedupes conflicting tailwind classes (last wins)", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
  });

  it("drops falsy values and keeps the rest", () => {
    expect(cn("text-primary", false && "hidden", "font-mono")).toBe(
      "text-primary font-mono",
    );
  });

  /* Regression: the role-named scale rungs are font sizes, not colours. Stock
     tailwind-merge files them under text-COLOR, so a colour class in the same
     string deletes the size. Keep both. */
  /* Every rung is covered on purpose. The registration had drifted from @theme —
     `dense` and `row` were absent, so a colour class silently deleted them (live in
     cross-run-context.tsx) — and it fails invisibly, because the class is DROPPED
     rather than overridden: the element renders at the inherited size and stops
     matching `.text-<rung>` at all. This is what catches the next drift. */
  it.each(["label", "caption", "dense", "body", "row", "lead"])(
    "keeps text-%s alongside a text colour",
    (rung) => {
      expect(cn(`text-${rung}`, "text-heading")).toBe(`text-${rung} text-heading`);
    },
  );

  it("keeps a scale size alongside a text colour in a mixed string", () => {
    expect(cn("text-label uppercase", "text-text-tertiary")).toBe(
      "text-label uppercase text-text-tertiary",
    );
    expect(cn("font-mono text-dense", "text-foreground")).toBe(
      "font-mono text-dense text-foreground",
    );
  });

  /* `text-data` is a COLOUR here (--color-data, the green data accent), not a rung.
     It used to sit in the font-size group, which claimed a real colour utility as a
     size. Two colours must resolve last-wins. */
  it("treats text-data as the colour it is, not a size", () => {
    expect(cn("text-data", "text-foreground")).toBe("text-foreground");
  });

  it("still lets a later scale rung override an earlier size", () => {
    expect(cn("text-caption", "text-body")).toBe("text-body");
    expect(cn("text-sm", "text-lead")).toBe("text-lead");
    expect(cn("text-lead", "text-xs")).toBe("text-xs");
  });
});
