/**
 * Display-only formatting for mission metadata. Pure functions — they never invent
 * data, they only normalise the strings already on the wire so the same value renders
 * consistently on the catalog card and the detail page.
 */

/** "lateral-movement" | "web_service" → "Lateral Movement" | "Web Service". */
export function titleCase(s: string): string {
  return s
    .replace(/[-_]+/g, " ")
    .trim()
    .replace(/\s+/g, " ")
    .split(" ")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/**
 * Normalise the environment field (`metadata.type`) to a user label. `lab` / `static`
 * are the only real values; anything else (a legacy bundle) is title-cased so it still
 * reads cleanly rather than showing a raw token.
 */
export function environmentLabel(type: string): string {
  const t = type.trim().toLowerCase();
  if (t === "lab") return "Lab";
  if (t === "static") return "Static";
  return titleCase(type);
}

/** True when the mission runs a live lab environment (vs a static, attachment-only one). */
export function isLab(type: string | null | undefined): boolean {
  return (type ?? "").trim().toLowerCase() === "lab";
}

/** Badge variant for a proficiency, derived from its tier: Novice/Advance Beginner read
 *  green, Competent amber, Proficient/Expert red. */
export function difficultyVariant(
  proficiency: string,
): "ok" | "default" | "err" | "muted" {
  const level = proficiencyLevel(proficiency);
  if (level == null) return "muted";
  if (level <= 2) return "ok";
  if (level === 3) return "default";
  return "err";
}

/**
 * Proficiency tier 1–5 for a token on the XORCISE ladder (Novice → Advance Beginner →
 * Competent → Proficient → Expert), or null when unrecognised. Checked most-specific first so
 * "Advance Beginner" reads as tier 2, not tier 1 via the "beginner" fallback. The dot-matrix
 * DifficultyBadge fills that many amber pips (of 5).
 */
export function proficiencyLevel(proficiency: string): 1 | 2 | 3 | 4 | 5 | null {
  const p = proficiency.trim().toLowerCase();
  if (/expert/.test(p)) return 5;
  if (/proficient/.test(p)) return 4;
  if (/competent/.test(p)) return 3;
  if (/advance/.test(p)) return 2; // "advance beginner"
  if (/novice/.test(p)) return 1;
  // legacy easy/medium/intermediate/hard scale, mapped onto the nearest new tier
  if (/beginner|easy|intro|basic|foundation/.test(p)) return 1;
  if (/intermediate|medium|moderate/.test(p)) return 2;
  if (/hard|advanced|elite|insane/.test(p)) return 4;
  return null;
}

const OP_LABELS: Record<string, string> = {
  equals: "Equals",
  eq: "Equals",
  contains: "Contains",
  matches: "Matches",
  present: "Is present",
  absent: "Is absent",
  gte: "At least",
  lte: "At most",
  gt: "Greater than",
  lt: "Less than",
};

const SOURCE_LABELS: Record<string, string> = {
  artifacts: "Artifacts",
  "otel-stats": "OTel stats",
  "observed-facts": "Observed facts",
};

/**
 * Turn a deterministic check into human-readable parts (frontend formatting only): a raw
 * `flag-correct: equals flag (artifacts)` becomes { title: "Flag Correct", rule: "Equals
 * flag", source: "Artifacts" }.
 */
export function describeCheck(c: {
  id: string;
  op: string;
  ref: string;
  source: string;
}): { title: string; rule: string; source: string } {
  const op = OP_LABELS[c.op.trim().toLowerCase()] ?? titleCase(c.op);
  const rule = c.ref ? `${op} ${c.ref}` : op;
  const source = SOURCE_LABELS[c.source.trim().toLowerCase()] ?? titleCase(c.source);
  return { title: titleCase(c.id), rule, source };
}
