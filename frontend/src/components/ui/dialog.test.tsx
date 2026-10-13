import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
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

function OpenDialog() {
  const [open, setOpen] = useState(true);
  return (
    <Dialog open={open} onClose={() => setOpen(false)} title="T">
      body
    </Dialog>
  );
}

describe("Dialog keyboard", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("closes on Escape and on no other key", () => {
    render(<OpenDialog />);
    fireEvent.keyDown(document, { key: "Enter" });
    act(() => vi.advanceTimersByTime(150));
    expect(screen.getByRole("dialog", { name: "T" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    act(() => vi.advanceTimersByTime(150));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

function FocusHarness() {
  const [open, setOpen] = useState(false);
  const [ticks, setTicks] = useState(0);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open</button>
      <Dialog open={open} onClose={() => setOpen(false)} title="T">
        <label>
          <input type="checkbox" onChange={() => setTicks(ticks + 1)} /> pick {ticks}
        </label>
        <button>Confirm</button>
      </Dialog>
    </>
  );
}

function openFromButton() {
  const opener = screen.getByRole("button", { name: "Open" });
  opener.focus();
  fireEvent.click(opener);
  return opener;
}

describe("Dialog focus", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("moves focus into the dialog when it opens", () => {
    render(<FocusHarness />);
    openFromButton();
    expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
  });

  it("keeps Tab and Shift+Tab inside the dialog", () => {
    render(<FocusHarness />);
    openFromButton();
    const close = screen.getByRole("button", { name: "Close" });
    const confirm = screen.getByRole("button", { name: "Confirm" });
    confirm.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(close);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(confirm);
  });

  it("returns focus to what opened it when it closes", () => {
    render(<FocusHarness />);
    const opener = openFromButton();
    fireEvent.keyDown(document, { key: "Escape" });
    act(() => vi.advanceTimersByTime(150));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(opener);
  });

  it("leaves focus where it is when the dialog's content re-renders", () => {
    render(<FocusHarness />);
    openFromButton();
    const box = screen.getByRole("checkbox", { name: /pick/ });
    box.focus();
    fireEvent.click(box);
    expect(screen.getByRole("checkbox", { name: "pick 1" })).toBe(document.activeElement);
  });
});
