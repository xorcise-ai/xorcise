import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOAST_EXIT_MS, useToastStore } from "./toasts";

describe("toast two-phase dismiss", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.setState({ toasts: [] });
  });
  afterEach(() => vi.useRealTimers());

  it("marks the toast leaving, then removes it after TOAST_EXIT_MS", () => {
    let id = "";
    act(() => {
      id = useToastStore.getState().push({ title: "t", tone: "ok" });
    });
    act(() => useToastStore.getState().dismiss(id));
    expect(useToastStore.getState().toasts[0]?.leaving).toBe(true);
    act(() => vi.advanceTimersByTime(TOAST_EXIT_MS));
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it("dismissing twice does not double-schedule", () => {
    let id = "";
    act(() => {
      id = useToastStore.getState().push({ title: "t", tone: "ok" });
    });
    act(() => {
      useToastStore.getState().dismiss(id);
      useToastStore.getState().dismiss(id);
    });
    act(() => vi.advanceTimersByTime(TOAST_EXIT_MS));
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});
