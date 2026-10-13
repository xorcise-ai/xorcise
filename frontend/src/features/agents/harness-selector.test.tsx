import { useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { HarnessSelector } from "./harness-selector";
import { useHarnessDescriptors } from "./use-harnesses";

/** The selector is controlled; this plays the register page's part (`null` = nothing chosen). */
function Controlled({
  initial = null,
  onChange,
}: {
  initial?: string | null;
  onChange?: (kind: string) => void;
}) {
  const [kind, setKind] = useState<string | null>(initial);
  return (
    <HarnessSelector
      value={kind}
      onChange={(next) => {
        setKind(next);
        onChange?.(next);
      }}
    />
  );
}

describe("HarnessSelector", () => {
  it("renders the OpenHands brand mark + name", () => {
    renderWithProviders(<HarnessSelector value="openhands" onChange={() => {}} />);
    const oh = screen.getByRole("radio", { name: /openhands/i });
    expect(oh).toBeInTheDocument();
    expect(oh.querySelector("svg")).not.toBeNull();
  });

  it("exposes the harness picker as a labelled radiogroup", () => {
    renderWithProviders(<HarnessSelector value={null} onChange={() => {}} />);
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
    renderWithProviders(<HarnessSelector value={null} onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: /codex/i }));
    expect(onChange).toHaveBeenCalledWith("codex");
  });

  // #141: every agent has a kind. Nothing is preselected for a new agent, and the page says so
  // instead of describing a "selected harness" that does not exist.
  it("selects nothing while no harness has been chosen, and asks for one", () => {
    renderWithProviders(<HarnessSelector value={null} onChange={() => {}} />);
    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).toHaveAttribute("aria-checked", "false");
    }
    expect(screen.getByText(/choose a harness/i)).toBeInTheDocument();
    expect(screen.queryByText(/selected harness/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/harness id/i)).toHaveValue("");
  });

  it("fills the Harness ID with generic when Generic is chosen, and leaves it editable", () => {
    const onChange = vi.fn();
    renderWithProviders(<Controlled onChange={onChange} />);
    const input = screen.getByLabelText(/harness id/i);
    expect(input).toHaveAttribute("readonly");

    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    expect(onChange).toHaveBeenLastCalledWith("generic");
    expect(screen.getByRole("radio", { name: /^generic/i })).toHaveAttribute("aria-checked", "true");
    expect(input).toHaveValue("generic");
    expect(input).not.toHaveAttribute("readonly");
    expect(screen.getByText(/replay with the generic adapter/i)).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /custom/i })).not.toBeInTheDocument();

    // Your own label: still the Generic card, now holding the label.
    fireEvent.change(input, { target: { value: "my-agent-cli" } });
    expect(onChange).toHaveBeenLastCalledWith("my-agent-cli");
    expect(screen.getByRole("radio", { name: /^generic/i })).toHaveAttribute("aria-checked", "true");
    // Clicking the already-chosen card keeps the label rather than resetting it.
    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    expect(input).toHaveValue("my-agent-cli");
  });

  it("keeps Generic chosen when its ID is cleared, which saves generic", () => {
    renderWithProviders(<Controlled initial="generic" />);
    fireEvent.change(screen.getByLabelText(/harness id/i), { target: { value: "" } });
    expect(screen.getByRole("radio", { name: /^generic/i })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/a blank id saves as generic/i)).toBeInTheDocument();
  });

  it("keeps the read-only Harness ID out of the tab order and its focus ring off", () => {
    // It only echoes the chosen card. A global :focus-visible rule outranks the field's own
    // `outline-none` (unlayered CSS beats Tailwind's layers), so a click drew the amber
    // selection ring on a field that ignores typing.
    renderWithProviders(<HarnessSelector value="codex" onChange={() => {}} />);
    const input = screen.getByLabelText(/harness id/i);
    expect(input).toHaveValue("codex");
    expect(input).toHaveAttribute("readonly");
    expect(input).toHaveAttribute("tabindex", "-1");
    expect(input.className).toMatch(/focus-visible:!outline-none/);
  });

  it("forwards typed input from the Generic kind field", () => {
    const onChange = vi.fn();
    renderWithProviders(<Controlled onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    fireEvent.change(screen.getByLabelText(/harness id/i), {
      target: { value: "claude-code" },
    });
    expect(onChange).toHaveBeenLastCalledWith("claude-code");
    // A built-in typed in full is that harness: its card takes over.
    expect(screen.getByRole("radio", { name: /claude code/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("renders each harness as a card with its descriptor", () => {
    renderWithProviders(<HarnessSelector value={null} onChange={() => {}} />);
    expect(screen.getByText(/no thinking traces exported/i)).toBeInTheDocument();
    expect(screen.getByText(/user prompts only, no agent messages/i)).toBeInTheDocument();
  });

  it("marks the selected card and no longer embeds the capability matrix", () => {
    renderWithProviders(<HarnessSelector value="codex" onChange={() => {}} />);
    const selected = screen.getByRole("radio", { name: /Codex CLI/ });
    expect(selected).toHaveAttribute("data-selected", "true");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("keeps the ID editable while a label that starts with a built-in slug is typed", () => {
    // Keystroke by keystroke, `codex-fork` passes through `codex`: that prefix shows the Codex
    // card (it is what would be saved) but must not lock the field mid-word.
    renderWithProviders(<Controlled />);
    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    const input = screen.getByLabelText(/harness id/i);
    fireEvent.focus(input);
    for (const typed of ["", "c", "co", "cod", "code", "codex", "codex-", "codex-f", "codex-fork"]) {
      fireEvent.change(input, { target: { value: typed } });
      expect(input).not.toHaveAttribute("readonly");
      if (typed === "codex") {
        expect(screen.getByRole("radio", { name: /codex cli/i })).toHaveAttribute("aria-checked", "true");
      }
    }
    expect(input).toHaveValue("codex-fork");
    expect(screen.getByRole("radio", { name: /^generic/i })).toHaveAttribute("aria-checked", "true");
  });

  it("settles a typed built-in slug into that harness once the field is left", () => {
    renderWithProviders(<Controlled />);
    fireEvent.click(screen.getByRole("radio", { name: /^generic/i }));
    const input = screen.getByLabelText(/harness id/i);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "codex" } });
    expect(input).not.toHaveAttribute("readonly");
    fireEvent.blur(input);
    expect(input).toHaveAttribute("readonly");
    expect(screen.getByRole("radio", { name: /codex cli/i })).toHaveAttribute("aria-checked", "true");
  });

  it("treats a padded built-in slug as that harness, as the server will", () => {
    renderWithProviders(<HarnessSelector value=" openhands " onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: /^openhands/i })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /^generic/i })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/verified replay adapter/i)).toBeInTheDocument();
  });

  it("follows `value` when it becomes your own label on a later render", () => {
    const { rerender } = renderWithProviders(
      <HarnessSelector value={null} onChange={() => {}} />,
    );
    expect(screen.getByRole("radio", { name: /^generic/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );

    rerender(<HarnessSelector value="my-cli" onChange={() => {}} />);

    expect(screen.getByRole("radio", { name: /^generic/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByLabelText(/harness id/i)).toHaveValue("my-cli");
  });

  // #141: a free-text kind no registered harness answers to is allowed (custom harness) but is
  // disclosed before submit, with the closest real harness one click away. The live region is
  // always mounted, so "quiet" means it is empty, not absent.
  describe("unrecognised custom kind", () => {
    const status = () => screen.getByRole("status");

    it("warns about a near-miss and offers the harness it resembles", async () => {
      const onChange = vi.fn();
      renderWithProviders(<HarnessSelector value="openhand" onChange={onChange} />);
      await waitFor(() =>
        expect(status()).toHaveTextContent(
          "“openhand” isn’t a registered harness. Did you mean OpenHands?",
        ),
      );
      // "May": the trace's own resource kind or a span fingerprint can still pick OpenHands.
      expect(status()).toHaveTextContent(/runs may replay with the generic adapter/i);
      // Only the message is announced; the button sits outside the live region.
      expect(within(status()).queryByRole("button")).not.toBeInTheDocument();
      expect(status()).not.toHaveTextContent(/use openhands/i);

      fireEvent.click(screen.getByRole("button", { name: "Use OpenHands" }));
      expect(onChange).toHaveBeenCalledWith("openhands");
    });

    it("matches a prefix the way the server does", async () => {
      renderWithProviders(<HarnessSelector value="claude" onChange={() => {}} />);
      expect(await screen.findByRole("button", { name: "Use Claude Code CLI" })).toBeInTheDocument();
    });

    it("warns without a suggestion for an unrelated custom kind", async () => {
      renderWithProviders(<HarnessSelector value="my-agent-cli" onChange={() => {}} />);
      await waitFor(() =>
        expect(status()).toHaveTextContent("“my-agent-cli” isn’t a registered harness."),
      );
      expect(status()).not.toHaveTextContent(/did you mean/i);
      expect(screen.queryByRole("button", { name: /^use /i })).not.toBeInTheDocument();
    });

    it("stays quiet for a registered or blank kind", async () => {
      const { rerender } = renderWithProviders(
        <HarnessSelector value="openhand" onChange={() => {}} />,
      );
      await waitFor(() => expect(status()).toHaveTextContent(/isn’t a registered harness/));

      rerender(<HarnessSelector value="generic" onChange={() => {}} />);
      expect(status()).toBeEmptyDOMElement();
      rerender(<HarnessSelector value="  openhands-x" onChange={() => {}} />);
      expect(status()).toHaveTextContent("“openhands-x” isn’t a registered harness.");
      rerender(<HarnessSelector value="   " onChange={() => {}} />);
      expect(status()).toBeEmptyDOMElement();
      expect(screen.getByText(/a blank id saves as generic/i)).toBeInTheDocument();
    });

    it("cannot judge, and so does not warn, once the harness list has failed", async () => {
      server.use(http.get("*/api/harnesses", () => HttpResponse.json({}, { status: 500 })));
      function ListState() {
        const { status: state } = useHarnessDescriptors();
        return <span data-testid="list-state">{state}</span>;
      }
      renderWithProviders(
        <>
          <HarnessSelector value="openhand" onChange={() => {}} />
          <ListState />
        </>,
      );
      await waitFor(() => expect(screen.getByTestId("list-state")).toHaveTextContent("error"));
      expect(status()).toBeEmptyDOMElement();
      expect(screen.getByText(/may replay with the generic adapter/i)).toBeInTheDocument();
    });
  });
});
