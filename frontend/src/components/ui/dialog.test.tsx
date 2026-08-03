import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Dialog } from "./dialog";

describe("Dialog animation", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("animates in when open", () => {
    render(
      <Dialog open onClose={() => {}} title="T">
        body
      </Dialog>,
    );
    expect(screen.getByRole("dialog").className).toContain("animate-backdrop-in");
  });

  it("plays the exit animation before unmounting", () => {
    const { rerender } = render(
      <Dialog open onClose={() => {}} title="T">
        body
      </Dialog>,
    );
    rerender(
      <Dialog open={false} onClose={() => {}} title="T">
        body
      </Dialog>,
    );
    expect(screen.getByRole("dialog").className).toContain("animate-backdrop-out");
    act(() => vi.advanceTimersByTime(150));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("ignores clicks during the exit window", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <Dialog open onClose={onClose} title="T">
        body
      </Dialog>,
    );
    rerender(
      <Dialog open={false} onClose={onClose} title="T">
        body
      </Dialog>,
    );
    fireEvent.click(screen.getByRole("dialog"));
    fireEvent.click(screen.getByLabelText("Close"));
    expect(onClose).not.toHaveBeenCalled();
  });
});
