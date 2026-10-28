"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

/**
 * How many fixed-minimum items fit across an element, re-measured as it resizes. The run strip
 * and the run ledger show an agent's LATEST runs, as many as their width can draw at a legible
 * size, so the same card shows more runs on a wide screen and fewer on a phone instead of
 * squeezing every run into hairlines or scrolling sideways.
 *
 * `minItem` is the narrowest an item may draw, `gap` the space between items, `max` a ceiling
 * past which more runs stop being readable as individuals. Before the first measurement (and
 * where there is no ResizeObserver, as in jsdom) it answers `fallback`.
 */
export function useFitCount<T extends HTMLElement>(
  minItem: number,
  gap: number,
  max: number,
  fallback: number,
): [RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [count, setCount] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const width = el.clientWidth;
      if (width <= 0) return;
      setCount(Math.max(1, Math.min(max, Math.floor((width + gap) / (minItem + gap)))));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [minItem, gap, max]);
  return [ref, count];
}
