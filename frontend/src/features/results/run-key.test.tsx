import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { RunKey } from "./run-key";

const group = (name: string) =>
  within(screen.getByRole("list", { name }))
    .getAllByRole("listitem")
    .map((li) => li.textContent);

describe("RunKey: what counts in the median, then what is left out", () => {
  it("groups the run strip's kinds on either side of the divider", () => {
    render(<RunKey view="strip" />);
    expect(group("In the median")).toEqual(["scored", "timed out", "partial judge"]);
    expect(group("Left out of the median")).toEqual([
      "no judge",
      "stopped",
      "grading failed",
      "environment failure",
      "grading",
    ]);
  });

  it("lists only what the agreement plot places: no environment failures or grading", () => {
    render(<RunKey view="plot" />);
    expect(group("In the median")).toEqual(["scored", "timed out", "partial judge"]);
    expect(group("Left out of the median")).toEqual(["no judge", "stopped"]);
  });

  it("draws each item with the view's own renderer, hidden from assistive technology", () => {
    const { container } = render(<RunKey view="ledger" />);
    for (const li of container.querySelectorAll("li[data-kind]")) {
      expect(li.firstElementChild!.getAttribute("aria-hidden")).toBe("true");
    }
    expect(group("Left out of the median")).toContain("environment failure");
  });

  it("outlines the kind of the run being inspected", () => {
    render(<RunKey view="strip" active="timed_out" />);
    const active = screen
      .getAllByRole("listitem")
      .filter((li) => li.hasAttribute("data-active"))
      .map((li) => li.dataset.kind);
    expect(active).toEqual(["timed_out"]);
  });
});

describe("How to read this", () => {
  it("opens the app's dialog on the view's anatomy, and Escape closes it, back to the trigger", async () => {
    render(<RunKey view="plot" />);
    const trigger = screen.getByRole("button", { name: "How to read this" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "How to read the agreement plot" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveTextContent("The diagonals join runs with the same overall score");
    expect(within(dialog).getByText("No judge")).toBeInTheDocument();
    expect(within(dialog).queryByText("Environment failure")).toBeNull();
    fireEvent.keyDown(document, { key: "Escape" });
    // The dialog plays its exit transition (150ms) before it unmounts.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });

  it("groups the kinds as the key does, and closes from Done", async () => {
    render(<RunKey view="strip" />);
    fireEvent.click(screen.getByRole("button", { name: "How to read this" }));
    const dialog = screen.getByRole("dialog", { name: "How to read the run strip" });
    expect(within(dialog).getByRole("heading", { name: "In the median" })).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "Left out" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent(
      "Not finishing counts against the agent, so it is in the median at the score it earned.",
    );
    expect(dialog).toHaveTextContent("An operator ended it, so its score says nothing about the agent.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("explains the ledger, environment failures included", () => {
    render(<RunKey view="ledger" />);
    fireEvent.click(screen.getByRole("button", { name: "How to read this" }));
    const dialog = screen.getByRole("dialog", { name: "How to read the run ledger" });
    expect(within(dialog).getByText("Environment failure")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("arrow keys");
  });
});
