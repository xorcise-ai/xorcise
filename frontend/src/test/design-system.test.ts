import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

/**
 * Design-system conformance, enforced mechanically.
 *
 * The console's tokens live in `src/app/globals.css` and its components in
 * `src/components/ui/`. Both were bypassed steadily and invisibly — 36 hardcoded colour
 * values and 9 raw Tailwind type sizes had accumulated across 29 files before anyone
 * measured. None of it was caught, because nothing looked. These three rules are the
 * cheapest possible guard: they read the source, so they need no browser and no server,
 * and each one names the exact token or utility that replaces what it rejects.
 */

const SRC = join(__dirname, "..");

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === "node_modules" || name === "test") continue;
      sourceFiles(p, out);
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

/** Comments explain what a rule retired; they are documentation, not call sites. */
function stripComments(s: string): string {
  return s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const FILES = sourceFiles(SRC).map((path) => ({
  path,
  rel: path.slice(SRC.length + 1),
  code: stripComments(readFileSync(path, "utf8")),
}));

describe("design system conformance", () => {
  it("declares every colour as a token — no hex or rgba() literals", () => {
    const offenders: string[] = [];
    for (const f of FILES) {
      f.code.split("\n").forEach((line, i) => {
        const m = line.match(/\[#[0-9a-fA-F]{3,8}\]|rgba\([\d\s.,]+\)/);
        if (m) offenders.push(`${f.rel}:${i + 1}  ${m[0]}`);
      });
    }
    expect(
      offenders,
      "Use a token (bg-card, text-heading, border-border), an opacity modifier on one " +
        "(bg-primary/10), or color-mix(in srgb, var(--color-x) N%, transparent) inside a " +
        "gradient or shadow. New values belong in globals.css, not at the call site.",
    ).toEqual([]);
  });

  it("sets every size from the type scale — no raw Tailwind text sizes", () => {
    const offenders: string[] = [];
    for (const f of FILES) {
      f.code.split("\n").forEach((line, i) => {
        const m = line.match(
          /\btext-(xs|sm|base|lg|xl|2xl|3xl|4xl|5xl|6xl)\b|\btext-\[[0-9.]+(px|rem)\]/,
        );
        if (m) offenders.push(`${f.rel}:${i + 1}  ${m[0]}`);
      });
    }
    expect(
      offenders,
      "Pick the ROLE that matches the job: text-label (eyebrows, badges), text-caption " +
        "(meta, help), text-dense (values, IDs), text-body (multi-line copy), text-row " +
        "(single-line rows), text-lead (page heads), text-stat (a StatTile figure), " +
        "text-display (the one hero number).",
    ).toEqual([]);
  });

  it("gives every grid a base column template", () => {
    // `grid` alone sets display:grid and NOTHING else, so a class list whose only
    // grid-cols-* utilities carry a breakpoint prefix has no column template below that
    // breakpoint. The implicit column then sizes to max-content and the row blows out —
    // this shipped on 21 grids, and made every card route overflow its pane at 375px.
    const offenders: string[] = [];
    const classAttr = /className=(?:"([^"]*)"|\{`([^`]*)`\}|\{cn\(\s*"([^"]*)")/g;
    for (const f of FILES) {
      f.code.split("\n").forEach((line, i) => {
        for (const m of line.matchAll(classAttr)) {
          const cls = m[1] ?? m[2] ?? m[3] ?? "";
          const toks = cls.split(/\s+/);
          if (!toks.includes("grid")) continue;
          const cols = toks.filter((t) => t.includes("grid-cols-"));
          if (cols.length && !cols.some((t) => !t.includes(":"))) {
            offenders.push(`${f.rel}:${i + 1}  ${cols.join(" ")}`);
          }
        }
      });
    }
    expect(
      offenders,
      "Add an unprefixed grid-cols-1 (or grid-cols-N) alongside the responsive variants.",
    ).toEqual([]);
  });

  /* The fourth rule, and the one that fails most quietly. tailwind-merge groups
     `text-<x>` as a COLOUR unless told otherwise, so any rung missing from cn.ts's
     font-size group is silently DELETED whenever a colour shares the merged string —
     the element renders at the inherited size and stops matching `.text-<rung>`.
     `stat` and `display` shipped that way: StatTile's cva pairs its size rung with a
     tone colour by construction, so every toned tile lost its figure size. Nothing
     caught it, because the class list still looked right in the source. Pinning the
     two lists against each other is the only check that survives a new rung. */
  it("registers every declared type rung with tailwind-merge", () => {
    const theme = readFileSync(join(SRC, "app", "globals.css"), "utf8");
    const declared = [
      ...new Set([...theme.matchAll(/^\s*--text-([a-z]+):/gm)].map((m) => m[1])),
    ].sort();

    const cn = readFileSync(join(SRC, "components", "ui", "cn.ts"), "utf8");
    const group = /"font-size":\s*\[[\s\S]*?text:\s*\[([\s\S]*?)\]/.exec(cn);
    expect(group, "cn.ts no longer declares a font-size class group").not.toBeNull();
    const registered = [...group![1].matchAll(/"([a-z]+)"/g)].map((m) => m[1]).sort();

    expect(
      registered,
      "the type rungs in globals.css @theme and the font-size group in " +
        "components/ui/cn.ts have drifted. A rung missing here is not overridden — " +
        "it is DROPPED from any cn() call that also carries a colour.",
    ).toEqual(declared);
  });

});
