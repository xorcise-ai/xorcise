import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { HarnessSelector } from "./harness-selector";

describe("HarnessSelector", () => {
  it("renders the OpenHands brand mark + name", () => {
    renderWithProviders(<HarnessSelector value="openhands" onChange={() => {}} />);
    const oh = screen.getByRole("radio", { name: /openhands/i });
    expect(oh).toBeInTheDocument();
    expect(oh.querySelector("svg")).not.toBeNull();
  });

  it("exposes the harness picker as a labelled radiogroup", () => {
    renderWithProviders(<HarnessSelector value="" onChange={() => {}} />);
    expect(screen.getByRole("radiogroup", { name: /agent harness/i })).toBeInTheDocument();
  });

  it("keeps selection accessible via aria-checked", () => {
    renderWithProviders(<HarnessSelector value="openhands" onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: /openhands/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("calls onChange with the harness kind when its card is clicked", () => {
    const onChange = vi.fn();
    renderWithProviders(<HarnessSelector value="" onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: /codex/i }));
    expect(onChange).toHaveBeenCalledWith("codex");
  });

  it("keeps the Harness ID field stable and unlocks it for Custom", () => {
    renderWithProviders(<HarnessSelector value="codex" onChange={() => {}} />);
    const input = screen.getByLabelText(/harness id/i);
    expect(input).toHaveValue("codex");
    expect(input).toHaveAttribute("readonly");

    fireEvent.click(screen.getByRole("radio", { name: /custom/i }));
    expect(input).not.toHaveAttribute("readonly");
  });

  it("forwards typed input from the Custom kind field", () => {
    const onChange = vi.fn();
    renderWithProviders(<HarnessSelector value="" onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: /custom/i }));
    fireEvent.change(screen.getByLabelText(/harness id/i), {
      target: { value: "claude-code" },
    });
    expect(onChange).toHaveBeenCalledWith("claude-code");
  });

  it("renders each harness as a card with its descriptor", () => {
    renderWithProviders(<HarnessSelector value="" onChange={() => {}} />);
    expect(screen.getByText(/no thinking traces exported/i)).toBeInTheDocument();
    expect(screen.getByText(/user prompts only, no agent messages/i)).toBeInTheDocument();
  });

  it("marks the selected card and no longer embeds the capability matrix", () => {
    renderWithProviders(<HarnessSelector value="codex" onChange={() => {}} />);
    const selected = screen.getByRole("radio", { name: /Codex CLI/ });
    expect(selected).toHaveAttribute("data-selected", "true");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  // Self-healing guard: `customMode` seeds itself at mount, but a `value` that arrives
  // non-built-in/non-empty on a LATER render (this component staying mounted while its parent
  // hands it a different `value` prop) must still flip into Custom mode.
  it("syncs into Custom mode when `value` becomes a custom kind on a later render", () => {
    const { rerender } = renderWithProviders(
      <HarnessSelector value="" onChange={() => {}} />,
    );
    expect(screen.getByRole("radio", { name: /custom/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );

    rerender(<HarnessSelector value="my-cli" onChange={() => {}} />);

    expect(screen.getByRole("radio", { name: /custom/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByLabelText(/harness id/i)).toHaveValue("my-cli");
  });
});
