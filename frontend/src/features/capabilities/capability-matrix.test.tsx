// Render with the MSW fixture from Task 8 + renderWithProviders from src/test.
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { CapabilityMatrix } from "./capability-matrix";

describe("CapabilityMatrix", () => {
  it("is a real table: group row headers + one column per harness + Generic", async () => {
    renderWithProviders(<CapabilityMatrix selectedKind="codex" />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByRole("rowheader", { name: /Thinking \/ CoT/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Codex CLI/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Generic/ })).toBeInTheDocument();
  });

  it("tells the honest story: codex messages partial, claude-code thinking unsupported", async () => {
    renderWithProviders(<CapabilityMatrix selectedKind="codex" />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByText(/agent-authored chat messages/)).toBeInTheDocument(); // codex note
    expect(screen.getAllByText(/not supported/).length).toBeGreaterThan(0);
  });

  it("shows refusal support separately from general status and metrics", async () => {
    renderWithProviders(<CapabilityMatrix selectedKind="codex" />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    expect(screen.getByRole("rowheader", { name: "Model refusals" })).toBeInTheDocument();
    expect(
      screen.getByText(
        /Model refusals: not supported, Model refusal details are not exported\./,
      ),
    ).toBeInTheDocument();
    // The two built-ins that export refusals; Generic's cell is unknown, not supported.
    expect(screen.getAllByText(/Model refusals: supported/)).toHaveLength(2);
  });

  it("marks every Generic cell unknown, never the generic adapter's profile as measured", async () => {
    renderWithProviders(<CapabilityMatrix selectedKind="codex" />);
    await waitFor(() =>
      expect(screen.getByText("(unknown — generic adapter, best effort)")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Thinking / CoT: unknown")).toBeInTheDocument();
    expect(screen.getByLabelText("Model refusals: unknown")).toBeInTheDocument();
  });

  it("highlights Generic when selectedKind matches no built-in harness (your own label)", async () => {
    renderWithProviders(<CapabilityMatrix selectedKind="my-custom-cli" />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    const genericHeader = screen.getByRole("columnheader", { name: /Generic/ });
    expect(genericHeader).toHaveAttribute("data-selected", "true");
    const codexHeader = screen.getByRole("columnheader", { name: /Codex CLI/ });
    expect(codexHeader).not.toHaveAttribute("data-selected");
  });

  it("highlights Generic for the generic kind", async () => {
    renderWithProviders(<CapabilityMatrix selectedKind="generic" />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByRole("columnheader", { name: /Generic/ })).toHaveAttribute(
      "data-selected",
      "true",
    );
  });
});
