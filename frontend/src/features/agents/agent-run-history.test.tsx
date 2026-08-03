import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type {
  AgentHistoryEntry,
  CatalogEntry,
  RunEntry,
} from "@/lib/api/types";
import { runFixture, missionFixture } from "@/test/fixtures";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { AgentRunHistory, buildAgentRunRows } from "./agent-run-history";

function historyEntry(over: Partial<AgentHistoryEntry> = {}): AgentHistoryEntry {
  return {
    agent_id: "agent-1",
    run_id: "run-xyz-123",
    created_at: "2026-06-29T10:00:00Z",
    overall: 0.5,
    deterministic: 0.4,
    judge: 0.6,
    partial: false,
    partial_trigger: null,
    conditions: {
      model: "claude-3-5-sonnet",
      agent_version: 1,
      mission_version: 1,
      budget_seconds: 0,
    },
    trace_ref: null,
    ...over,
  } as AgentHistoryEntry;
}

describe("buildAgentRunRows", () => {
  it("joins history with the runs list + catalog for mission, status, specialty", () => {
    const runs: RunEntry[] = [
      runFixture({
        run_id: "run-xyz-123",
        mission: "sqli-login",
        state: "terminal",
        terminal_trigger: "done",
      }),
    ];
    const catalog: CatalogEntry[] = [
      missionFixture({
        mission_id: "sqli-login",
        name: "SQLi Login Bypass",
        specialty: "web",
        proficiency: "beginner",
      }),
    ];
    const [row] = buildAgentRunRows([historyEntry()], runs, catalog);
    expect(row.missionName).toBe("SQLi Login Bypass");
    expect(row.status.label).toBe("Completed");
    expect(row.status.tone).toBe("green");
    expect(row.specialty).toBe("web");
    expect(row.proficiency).toBe("beginner");
  });

  it("reconstructs a fallback status from the partial flag when the run is gone", () => {
    const [row] = buildAgentRunRows([
      historyEntry({ partial: true, partial_trigger: "timeout" }),
    ]);
    expect(row.missionName).toBe("Unknown mission");
    expect(row.status.label).toBe("Timeout");
  });

  it("orders rows newest run first", () => {
    const rows = buildAgentRunRows([
      historyEntry({ run_id: "old", created_at: "2026-06-01T00:00:00Z" }),
      historyEntry({ run_id: "new", created_at: "2026-06-05T00:00:00Z" }),
    ]);
    expect(rows.map((r) => r.runId)).toEqual(["new", "old"]);
  });
});

describe("AgentRunHistory", () => {
  it("renders a compact row that links to the run result, not the raw run id", () => {
    const rows = buildAgentRunRows(
      [historyEntry({ run_id: "run-xyz-123" })],
      [
        runFixture({
          run_id: "run-xyz-123",
          mission: "sqli-login",
          state: "terminal",
          terminal_trigger: "done",
        }),
      ],
      [missionFixture({ mission_id: "sqli-login", name: "SQLi Login Bypass" })],
    );
    render(<AgentRunHistory rows={rows} />);
    // The mission name is the link through to the run result.
    const link = screen.getAllByRole("link", { name: /SQLi Login Bypass/i })[0];
    expect(link).toHaveAttribute("href", "/runs/result?id=run-xyz-123");
    // The bare, opaque run id is never rendered.
    expect(screen.queryByText("run-xyz-123")).toBeNull();
  });

  it("shows the §17 empty state when there are no runs", () => {
    render(<AgentRunHistory rows={[]} />);
    expect(
      screen.getByText(/No runs yet for this agent/i),
    ).toBeInTheDocument();
  });

  it("renders scores in the table", () => {
    const rows = buildAgentRunRows([
      historyEntry({ overall: 0.8, deterministic: 0.9, judge: 0.7 }),
    ]);
    render(<AgentRunHistory rows={rows} />);
    const table = screen.getByRole("table");
    expect(within(table).getByText("80%")).toBeInTheDocument();
    expect(within(table).getByText("90%")).toBeInTheDocument();
    expect(within(table).getByText("70%")).toBeInTheDocument();
  });
});
