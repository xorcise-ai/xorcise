import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import type { RunStats } from "@/lib/api/types";
import { TranscriptSummary, RunActivity } from "./run-activity";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// TerrainMap self-fetches terrain; stub it so this test targets the Activity composition, not the
// map internals (which have their own tests).
vi.mock("@/features/live-trace/terrain-map", () => ({
  TerrainMap: () => <div data-testid="terrain-map" />,
}));

function stats(over: Partial<RunStats["counts"]> = {}): RunStats {
  return {
    tokens: { input: 0, output: 0, cache_read: 0, cache_creation: 0, reasoning: 0, total: 0 },
    counts: {
      model_calls: 4,
      tool_calls: 9,
      findings: 2,
      errors: 1,
      events_total: 30,
      by_kind: {},
      ...over,
    },
    timing: { elapsed_seconds: 10, first_event_ts: null, last_event_ts: null, longest_tool_ms: null },
    cost_estimated_usd: null,
  };
}

describe("TranscriptSummary", () => {
  it("renders the counts", () => {
    render(<TranscriptSummary stats={stats()} />);
    expect(screen.getByText("Model calls")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument(); // tool calls
    expect(screen.getByText("2")).toBeInTheDocument(); // findings
  });

  it("shows a placeholder when no telemetry was captured", () => {
    render(<TranscriptSummary stats={undefined} />);
    expect(screen.getByText(/No transcript telemetry/i)).toBeInTheDocument();
  });

  it("shows the placeholder for an empty projection", () => {
    render(<TranscriptSummary stats={stats({ events_total: 0 })} />);
    expect(screen.getByText(/No transcript telemetry/i)).toBeInTheDocument();
  });
});

describe("RunActivity", () => {
  it("renders the terrain map and a link to the full trace", async () => {
    server.use(
      http.get("*/api/runs/:id/stats", () => HttpResponse.json(stats())),
      http.get("*/api/config", () =>
        HttpResponse.json({ terrain: { configured: true } }),
      ),
    );
    renderWithProviders(<RunActivity runId="r1" />);
    expect(await screen.findByTestId("terrain-map")).toBeInTheDocument();
    const link = screen.getByText(/View full trace/i).closest("a");
    expect(link?.getAttribute("href")).toContain("/runs/live?id=r1");
  });
});
