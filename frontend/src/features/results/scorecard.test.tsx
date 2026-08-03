import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { GradeResult } from "@/lib/api/types";
import { Scorecard, scoreTone } from "./scorecard";

function grade(over: Partial<GradeResult> = {}): GradeResult {
  return {
    run_id: "r1",
    overall: 0.55,
    breakdown: { deterministic: 1, judge: 0.1 },
    artifacts: [],
    key_evidence: [],
    major_deductions: [],
    hard_fails: [],
    judge_status: "partial",
    judge_detail: null,
    judge_prompt: null,
    judge_breakdown: [],
    check_breakdown: [],
    spans_truncated: 0,
    judge_upper: 0.7,
    judge_coverage: 0.4,
    overall_upper: 0.85,
    ...over,
  };
}

describe("Scorecard", () => {
  it("labels a partial judge bar as a conservative range with coverage", () => {
    const r = grade();
    render(<Scorecard grade={r} tone={scoreTone(r.overall)} />);
    expect(
      screen.getByText(/conservative lower bound; possible range 10%–70% at 40% coverage/i),
    ).toBeInTheDocument();
  });
});
