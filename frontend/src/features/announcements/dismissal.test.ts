import { describe, it, expect, afterEach, vi } from "vitest";
import {
  DISMISSED_STORAGE_KEY,
  dismiss,
  isDismissed,
} from "./dismissal";

/**
 * The storage half of dismissal, tested without React.
 *
 * `useDismissal` is a thin wrapper over these two functions, and the behaviour worth pinning
 * — a dismissal that outlives the page, a revision bump that undoes it, a browser where
 * storage is unavailable — lives here. The hook's own contract (first render is always
 * `false`, so a static export can hydrate) is asserted where it matters, in
 * `application-announcement.test.tsx`, against a real remount with a fresh QueryClient.
 */

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("isDismissed / dismiss", () => {
  it("remembers a dismissal across a page load", () => {
    expect(isDismissed("a1", 1)).toBe(false);
    dismiss("a1", 1, ["a1"]);
    // A fresh read from storage — this is exactly what the next document load does.
    expect(isDismissed("a1", 1)).toBe(true);
  });

  it("brings an announcement back when its revision is bumped", () => {
    dismiss("a1", 1, ["a1"]);
    expect(isDismissed("a1", 1)).toBe(true);
    // The publisher edited the banner. EQUALS, not >=: the edit is the publisher saying this
    // now says something the reader has not seen, and the reader is owed the new words even
    // though they dismissed the old ones.
    expect(isDismissed("a1", 2)).toBe(false);
    dismiss("a1", 2, ["a1"]);
    expect(isDismissed("a1", 2)).toBe(true);
    expect(isDismissed("a1", 1)).toBe(false);
  });

  it("keeps the two live placements and drops ids that are no longer served", () => {
    dismiss("app-1", 1, ["app-1", "cat-1"]);
    dismiss("cat-1", 3, ["app-1", "cat-1"]);
    expect(isDismissed("app-1", 1)).toBe(true);
    expect(isDismissed("cat-1", 3)).toBe(true);

    // Both are withdrawn and a new one is published. Without the prune this key would grow by
    // one entry per announcement ever published, forever, in every browser.
    dismiss("app-2", 1, ["app-2"]);
    expect(isDismissed("app-1", 1)).toBe(false);
    expect(isDismissed("cat-1", 3)).toBe(false);
    expect(isDismissed("app-2", 1)).toBe(true);
    expect(Object.keys(JSON.parse(window.localStorage.getItem(DISMISSED_STORAGE_KEY)!))).toEqual(
      ["app-2"],
    );
  });

  it("writes under the documented key, in the documented shape", () => {
    dismiss("a1", 7, ["a1"]);
    expect(JSON.parse(window.localStorage.getItem(DISMISSED_STORAGE_KEY)!)).toEqual({ a1: 7 });
  });
});

describe("when localStorage is hostile", () => {
  it("degrades silently when reading throws (embedded webview, cookies blocked)", () => {
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    // Not "assume dismissed" and not a crash: the banner shows, which is the safe default
    // for something a publisher wanted read.
    expect(() => isDismissed("a1", 1)).not.toThrow();
    expect(isDismissed("a1", 1)).toBe(false);
  });

  it("degrades silently when writing throws (Safari private mode)", () => {
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    // The click must not throw — a dismiss control that throws leaves the reader with a
    // banner they cannot remove and a broken page. The dismissal just does not outlive the
    // page, which is the most that browser will allow.
    expect(() => dismiss("a1", 1, ["a1"])).not.toThrow();
  });

  it("ignores a corrupt or hand-edited value instead of throwing", () => {
    window.localStorage.setItem(DISMISSED_STORAGE_KEY, "not json at all");
    expect(isDismissed("a1", 1)).toBe(false);

    window.localStorage.setItem(DISMISSED_STORAGE_KEY, '["a1"]');
    expect(isDismissed("a1", 1)).toBe(false);

    // A well-formed object with a junk value: the good entries still work.
    window.localStorage.setItem(DISMISSED_STORAGE_KEY, '{"a1":"1","a2":2}');
    expect(isDismissed("a1", 1)).toBe(false);
    expect(isDismissed("a2", 2)).toBe(true);
  });
});
