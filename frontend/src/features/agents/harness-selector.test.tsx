import { describe, it, expect, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { HarnessSelector } from "./harness-selector";
import { useHarnessDescriptors } from "./use-harnesses";

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
      expect(status()).toHaveTextContent(/replay with the generic adapter/i);

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
      expect(screen.getByText(/enter the trace source identifier/i)).toBeInTheDocument();
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
      expect(screen.getByText(/enter the trace source identifier/i)).toBeInTheDocument();
    });
  });
});
