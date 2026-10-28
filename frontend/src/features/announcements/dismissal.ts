"use client";

import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { useAnnouncements } from "./queries";

/**
 * `useLayoutEffect` in the browser, `useEffect` on the server.
 *
 * React warns that `useLayoutEffect` does nothing during server rendering, and the warning is
 * right — so this picks the effect that exists wherever it runs. The static export prerenders
 * at build time, where `window` is absent and the plain effect is correct and silent.
 */
const useIsomorphicLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * localStorage key recording which announcements this browser has dismissed, and at which
 * revision. `xorcise:<domain>:<thing>`, the convention VIEW_STORAGE_KEY in the mission
 * catalog set.
 */
export const DISMISSED_STORAGE_KEY = "xorcise:announcements:dismissed";

/** id -> the revision of that announcement the reader dismissed. */
type DismissedMap = Record<string, number>;

/**
 * Every storage access in this file is wrapped.
 *
 * `localStorage` is not a plain object: reading the property at all throws a SecurityError in
 * an embedded webview with cookies blocked, `setItem` throws QuotaExceededError in Safari's
 * private mode, and a value another tab or an older build wrote can be anything at all, so
 * `JSON.parse` throws too. None of those are conditions this feature can do anything about,
 * but all of them are conditions where an unhandled throw would take out the banner — and, in
 * the dismiss handler, take out the click that was supposed to make the banner go away. So a
 * failed read degrades to "nothing was dismissed" and a failed write degrades to "this
 * dismissal does not outlive the page", which are the right answers for a banner.
 *
 * The rest of the app does not do this yet (the catalog's view toggle calls
 * `localStorage.setItem` bare). It is worth doing here because a broken view toggle is a
 * forgotten preference, while a broken dismissal is a banner the reader cannot get rid of.
 */
function readMap(): DismissedMap {
  try {
    const raw = window.localStorage.getItem(DISMISSED_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: DismissedMap = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "number") out[key] = value;
    }
    return out;
  } catch {
    return {};
  }
}

function writeMap(map: DismissedMap): void {
  try {
    window.localStorage.setItem(DISMISSED_STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* see readMap — a dismissal that cannot be stored still hides the banner for this page */
  }
}

/**
 * Has this exact revision been dismissed?
 *
 * EQUALS, not `>=`. Editing a published announcement bumps its revision, and the edit is the
 * publisher saying the banner now says something the reader has not seen — so a bumped
 * revision reappears even for a reader who dismissed the previous one. A `>=` comparison
 * would silently swallow every future correction to an announcement anyone had dismissed
 * once, which is the failure mode that matters: the incident update nobody reads.
 */
export function isDismissed(id: string, revision: number): boolean {
  return readMap()[id] === revision;
}

/**
 * Record a dismissal, and drop the ids that are no longer live.
 *
 * Announcements are transient and their ids are not reused, so without the prune this key
 * grows by one entry per announcement ever published, forever, in every browser — a slow leak
 * nobody would ever look for. `activeIds` is the set currently being served, which is the only
 * evidence available about what is still worth remembering.
 */
export function dismiss(id: string, revision: number, activeIds: readonly string[]): void {
  const live = new Set([...activeIds, id]);
  const next: DismissedMap = {};
  for (const [key, value] of Object.entries(readMap())) {
    if (live.has(key)) next[key] = value;
  }
  next[id] = revision;
  writeMap(next);
}

/**
 * `[dismissed, dismissNow]` for one announcement.
 *
 * The initial state is `false` and the stored value is adopted in an effect, NOT read during
 * render. This app is a static export: the markup is generated at build time, when no
 * browser and no storage exists, so a render-time read would make the first client render
 * disagree with the server HTML and React would discard the tree as a hydration mismatch.
 * `catalog.tsx` documents the same trick for the grid/list toggle.
 *
 * The effect is a LAYOUT effect, which is what removes the cost of that trick. A passive
 * `useEffect` runs after the browser has painted, so a banner the reader dismissed appeared
 * for one frame on every document load and then vanished — and because the catalog banner
 * unmounts with its tab, it flashed again on every return to the XORCISE Remote tab, shifting
 * the mission grid down and back each time. A layout effect runs after the DOM is mutated but
 * BEFORE paint, so the removal lands in the same frame and nothing is ever shown.
 *
 * That is safe here precisely because it changes nothing about hydration: the first render
 * still returns the same markup the build produced, and the storage read still happens after
 * it, not during. The layout effect only moves the correction earlier within the client's own
 * commit — it does not move it before the render that hydration compares.
 *
 * The active set for the prune comes from the shared announcements query, which both
 * placements already consume — reading it here costs no extra request (see `queries.ts`).
 */
export function useDismissal(id: string, revision: number): [boolean, () => void] {
  const { data } = useAnnouncements();
  const [dismissed, setDismissed] = useState(false);

  useIsomorphicLayoutEffect(() => {
    setDismissed(isDismissed(id, revision));
  }, [id, revision]);

  const dismissNow = useCallback(() => {
    setDismissed(true);
    dismiss(id, revision, (data?.announcements ?? []).map((a) => a.id));
  }, [id, revision, data]);

  return [dismissed, dismissNow];
}
