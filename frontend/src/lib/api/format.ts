/** Format a 0..1 score as a whole-number percentage. */
export function pct(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `${Math.round(n * 100)}%`;
}

/** Human-ish timestamp (keeps the ISO date, trims to minutes). */
export function shortTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.replace("T", " ").replace(/:\d{2}(\.\d+)?Z?$/, "");
}

/**
 * Absolute run timestamp as `2026-07-26 07:48:00 UTC` — the one format shared by the results page,
 * the live run header and the HTML/MD exports. Stored times are UTC, so the date/time is taken
 * straight from the ISO string; a non-ISO value falls back to the UTC components of the parsed date.
 */
export function fullTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/);
  if (m) return `${m[1]} ${m[2]} UTC`;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())} UTC`
  );
}

/** Elapsed run duration as a compact `45s` / `4m 30s` / `1h 2m 3s`. Shared across run surfaces. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const t = Math.max(0, Math.floor(seconds));
  if (t < 60) return `${t}s`;
  const m = Math.floor(t / 60);
  const s = t % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m ${s}s`;
}
