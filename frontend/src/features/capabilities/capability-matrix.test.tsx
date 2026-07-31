// Render with the MSW fixture from Task 8 + renderWithProviders from src/test.
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { CapabilityMatrix } from "./capability-matrix";

describe("CapabilityMatrix", () => {
  it("is a real table: group row headers + one column per harness + Custom", async () => {
    renderWithProviders(<CapabilityMatrix selectedKind="codex" />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByRole("rowheader", { name: /Thinking \/ CoT/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Codex CLI/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Custom/ })).toBeInTheDocument();
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
    expect(screen.getAllByText(/Model refusals: supported/)).toHaveLength(3);
  });

  it("marks the Custom column unverified", async () => {
    renderWithProviders(<CapabilityMatrix selectedKind="codex" />);
    await waitFor(() => expect(screen.getByText(/unverified/i)).toBeInTheDocument());
  });

  it("highlights Custom when selectedKind matches no built-in harness (typed custom kind)", async () => {
    renderWithProviders(<CapabilityMatrix selectedKind="my-custom-cli" />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    const customHeader = screen.getByRole("columnheader", { name: /Custom/ });
    expect(customHeader).toHaveAttribute("data-selected", "true");
    const codexHeader = screen.getByRole("columnheader", { name: /Codex CLI/ });
    expect(codexHeader).not.toHaveAttribute("data-selected");
  });

  it("still highlights Custom for the unselected default (selectedKind=\"\")", async () => {
    renderWithProviders(<CapabilityMatrix selectedKind="" />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByRole("columnheader", { name: /Custom/ })).toHaveAttribute(
      "data-selected",
      "true",
    );
  });
});
