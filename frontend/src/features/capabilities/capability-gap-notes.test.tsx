import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { CapabilityGapNotes } from "./capability-gap-notes";

describe("CapabilityGapNotes", () => {
  it("spells out the selected harness's noted gaps as visible text", async () => {
    renderWithProviders(<CapabilityGapNotes selectedKind="codex" />);
    await waitFor(() =>
      expect(
        screen.getByText(/User prompts only — Codex CLI does not export agent-authored chat messages\./),
      ).toBeInTheDocument(),
    );
    // Partial group carries its label + a partial badge; thinking gap is spelled out too.
    expect(screen.getByText(/Agent messages/)).toBeInTheDocument();
    expect(screen.getByText(/partial/i)).toBeInTheDocument();
    expect(screen.getByText(/Codex CLI does not export thinking traces\./)).toBeInTheDocument();
  });

  it("omits un-noted structural gaps (they stay matrix-only)", async () => {
    renderWithProviders(<CapabilityGapNotes selectedKind="codex" />);
    await waitFor(() => expect(screen.getByText(/Agent messages/)).toBeInTheDocument());
    expect(screen.queryByText(/Browser/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Findings & flags/)).not.toBeInTheDocument();
  });

  it("shows the unverified-generic line for a custom kind", async () => {
    renderWithProviders(<CapabilityGapNotes selectedKind="my-custom-cli" />);
    await waitFor(() =>
      expect(
        screen.getByText(/Unknown harness — generic adapter; telemetry profile not verified\./),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Agent messages/)).not.toBeInTheDocument();
  });

  it("shows the unknown-harness line on API error (no Skeleton)", async () => {
    server.use(
      http.get("*/api/harnesses/capabilities", () =>
        HttpResponse.json({ detail: "server error" }, { status: 500 }),
      ),
    );
    renderWithProviders(<CapabilityGapNotes selectedKind="codex" />);
    await waitFor(() =>
      expect(
        screen.getByText(/Unknown harness — generic adapter; telemetry profile not verified\./),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("renders the partial badge with warn styling (no primary/amber)", async () => {
    renderWithProviders(<CapabilityGapNotes selectedKind="codex" />);
    await waitFor(() => expect(screen.getByText(/Agent messages/)).toBeInTheDocument());
    const badge = screen.getByText(/partial/i);
    expect(badge.getAttribute("class") ?? "").not.toMatch(/primary|amber/);
  });

  // openhands has no disclosed (partial/unsupported-with-note) groups, but the matrix above it
  // still shows struck structural gaps — the empty-state copy must not claim there are none.
  it("doesn't contradict the matrix's structural gaps when a harness has no disclosed notes", async () => {
    renderWithProviders(<CapabilityGapNotes selectedKind="openhands" />);
    await waitFor(() =>
      expect(
        screen.getByText(/No further notes — structural gaps are shown in the matrix above\./),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/no disclosed gaps/i)).not.toBeInTheDocument();
  });
});
