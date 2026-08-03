import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { GradeResult, RunStats, RunEntry } from "@/lib/api/types";
import { KpiStrip } from "./kpi-strip";

function grade(over: Partial<GradeResult> = {}): GradeResult {
  return {
    run_id: "r1",
    overall: 0.72,
    breakdown: { deterministic: 0.8, judge: 0.64 },
    artifacts: [],
    trace_ref: "r1",
    key_evidence: [],
    major_deductions: [],
    hard_fails: [],
    judge_status: "ok",
    judge_detail: null,
    judge_prompt: null,
    judge_breakdown: [],
    check_breakdown: [],
    spans_truncated: 0,
    ...over,
  };
}

function stats(over: Partial<RunStats> = {}): RunStats {
  return {
    tokens: {
      input: 12000,
      output: 1300,
      cache_read: 0,
      cache_creation: 0,
      reasoning: 0,
      total: 13300,
    },
    counts: {
      model_calls: 4,
      tool_calls: 9,
      findings: 0,
      errors: 0,
      events_total: 20,
      by_kind: {},
    },
    timing: {
      elapsed_seconds: 90,
      first_event_ts: null,
      last_event_ts: null,
      longest_tool_ms: null,
    },
    cost_estimated_usd: null,
    ...over,
  };
}

const run = { created_at: "2026-01-01T00:00:00Z", completed_at: null } as RunEntry;

describe("KpiStrip", () => {
  it("renders score tiles from the grade", () => {
    render(<KpiStrip grade={grade()} stats={stats()} run={run} />);
    expect(screen.getByText("72%")).toBeInTheDocument(); // overall
    expect(screen.getByText("80%")).toBeInTheDocument(); // deterministic
    expect(screen.getByText("64%")).toBeInTheDocument(); // judge
  });

  it("renders conservative score ranges when the judge is partial", () => {
    render(
      <KpiStrip
        grade={grade({ overall: 0.5, overall_upper: 0.8, judge_upper: 0.9 })}
        stats={stats()}
        run={run}
      />,
    );
    expect(screen.getByText("50%–80%")).toBeInTheDocument();
    expect(screen.getByText("64%–90%")).toBeInTheDocument();
  });

  it("renders telemetry tiles from stats", () => {
    render(<KpiStrip grade={grade()} stats={stats()} run={run} />);
    expect(screen.getByText("13.3k")).toBeInTheDocument(); // total tokens
    expect(screen.getByText("9")).toBeInTheDocument(); // tool calls
    expect(screen.getByText("4")).toBeInTheDocument(); // model calls
    expect(screen.getByText("1m 30s")).toBeInTheDocument(); // elapsed
  });

  it("shows — for telemetry tiles when stats are absent", () => {
    render(<KpiStrip grade={grade()} stats={undefined} run={run} />);
    // score tiles still present
    expect(screen.getByText("72%")).toBeInTheDocument();
    // token/tool/model tiles degrade to em-dash, never NaN/0
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByText("NaN")).not.toBeInTheDocument();
  });

  it("degrades gracefully on a gradeless stats placeholder (mid-re-evaluate), never crashing", () => {
    // A terminal-ungraded run 202s a {run_id, status:"grading"} body with no tokens/counts/timing.
    // The strip must read through the missing nested fields as "—", not throw.
    const placeholder = { run_id: "r1", status: "grading" } as unknown as RunStats;
    render(<KpiStrip grade={grade()} stats={placeholder} run={run} />);
    expect(screen.getByText("72%")).toBeInTheDocument(); // scores still render
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByText("NaN")).not.toBeInTheDocument();
  });

  it("shows — for zero total tokens (no telemetry captured)", () => {
    render(
      <KpiStrip
        grade={grade()}
        stats={stats({
          tokens: {
            input: 0,
            output: 0,
            cache_read: 0,
            cache_creation: 0,
            reasoning: 0,
            total: 0,
          },
        })}
        run={run}
      />,
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
