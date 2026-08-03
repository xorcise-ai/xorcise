import { StrictMode } from "react";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useExitTransition } from "./use-exit-transition";

describe("useExitTransition", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("mounts immediately when open", () => {
    const { result } = renderHook(() => useExitTransition(true));
    expect(result.current).toEqual({ mounted: true, closing: false });
  });

  it("stays mounted (closing) for ms after open flips false, then unmounts", () => {
    const { result, rerender } = renderHook(
      ({ open }) => useExitTransition(open, 150),
      { initialProps: { open: true } },
    );
    rerender({ open: false });
    expect(result.current).toEqual({ mounted: true, closing: true });
    act(() => vi.advanceTimersByTime(150));
    expect(result.current).toEqual({ mounted: false, closing: false });
  });

  it("cancels the pending unmount if reopened mid-exit", () => {
    const { result, rerender } = renderHook(
      ({ open }) => useExitTransition(open, 150),
      { initialProps: { open: true } },
    );
    rerender({ open: false });
    rerender({ open: true });
    act(() => vi.advanceTimersByTime(300));
    expect(result.current).toEqual({ mounted: true, closing: false });
  });

  it("never mounts if opened=false from the start", () => {
    const { result } = renderHook(() => useExitTransition(false));
    expect(result.current.mounted).toBe(false);
  });

  it("reopen mid-exit cancels the unmount under StrictMode", () => {
    const { result, rerender } = renderHook(
      ({ open }) => useExitTransition(open, 150),
      { initialProps: { open: true }, wrapper: StrictMode },
    );
    rerender({ open: false });
    rerender({ open: true });
    act(() => vi.advanceTimersByTime(300));
    expect(result.current).toEqual({ mounted: true, closing: false });
  });
});
