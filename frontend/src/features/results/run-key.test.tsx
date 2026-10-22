import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { RunKey } from "./run-key";

const items = () =>
  within(screen.getByRole("list", { name: "Key" }))
    .getAllByRole("listitem")
    // The ledger's environment-failure swatch draws a ×; it is aria-hidden, so read past it.
    .map((li) => li.textContent?.replace("×", ""));

describe("RunKey: one key per view, drawn with the view's own shapes", () => {
  it("lists every kind of run the run strip draws, and its median line", () => {
    render(<RunKey view="strip" />);
    expect(items()).toEqual([
      "judge · checks, height = overall",
      "scored",
      "partial judge",
      "no judge",
      "timed out or stopped",
      "environment failure",
      "grading",
      "median",
    ]);
  });

  it("lists only what the agreement plot places: no environment failures or grading", () => {
    render(<RunKey view="plot" />);
    expect(items()).toEqual([
      "checks →judge ↑",
      "scored",
      "partial judge",
      "no judge",
      "timed out or stopped",
    ]);
  });

  it("explains the ledger's two shades", () => {
    render(<RunKey view="ledger" />);
    expect(items()[0]).toBe("judge · checks, brighter = higher");
    expect(items()).toContain("environment failure");
  });

  it("keeps the swatches out of the accessibility tree: the words carry the meaning", () => {
    const { container } = render(<RunKey view="strip" />);
    for (const li of container.querySelectorAll("li")) {
      for (const swatch of li.querySelectorAll("span:first-child")) {
        if (swatch.childElementCount > 0) expect(swatch.closest("[aria-hidden]")).not.toBeNull();
      }
    }
  });
});

describe("How to read this", () => {
  it("opens the view's anatomy, and Escape closes it and returns focus to the trigger", () => {
    render(<RunKey view="plot" />);
    const trigger = screen.getByRole("button", { name: "How to read this" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("dialog", { name: "How to read this" });
    expect(panel).toHaveTextContent("The diagonals join runs with the same overall score");
    expect(within(panel).getByText("No judge")).toBeInTheDocument();
    expect(within(panel).queryByText("Environment failure")).toBeNull();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("closes on a press outside it, and from its close button", () => {
    render(
      <>
        <RunKey view="strip" />
        <p>elsewhere</p>
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "How to read this" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("median");
    fireEvent.mouseDown(screen.getByText("elsewhere"));
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "How to read this" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("explains each kind of run in the ledger, environment failures included", () => {
    render(<RunKey view="ledger" />);
    fireEvent.click(screen.getByRole("button", { name: "How to read this" }));
    const panel = screen.getByRole("dialog");
    expect(within(panel).getByText("Environment failure")).toBeInTheDocument();
    expect(panel).toHaveTextContent("arrow keys");
  });
});
