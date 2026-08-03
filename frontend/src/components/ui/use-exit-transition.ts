import { useEffect, useRef, useState } from "react";

/** Keeps a closing element mounted for `ms` so its exit keyframes can play.
 *  Dialog/ToastHost unmount instantly today; this is the seam that gives CSS
 *  exit animations time to run. Reopening mid-exit cancels the unmount. */
export function useExitTransition(
  open: boolean,
  ms = 150,
): { mounted: boolean; closing: boolean } {
  const [mounted, setMounted] = useState(open);
  const [closing, setClosing] = useState(false);
  const mountedRef = useRef(open);

  useEffect(() => {
    if (open) {
      mountedRef.current = true;
      setMounted(true);
      setClosing(false);
      return;
    }
    if (!mountedRef.current) return;
    setClosing(true);
    const t = setTimeout(() => {
      mountedRef.current = false;
      setMounted(false);
      setClosing(false);
    }, ms);
    return () => clearTimeout(t);
  }, [open, ms]);

  return { mounted, closing };
}
