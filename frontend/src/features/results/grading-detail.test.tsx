import { describe, it, expect } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import type { CriterionScore, GradeResult } from "@/lib/api/types";
import { GradingDetail } from "./grading-detail";

function criterion(over: Partial<CriterionScore> = {}): CriterionScore {
  return {
    criterion_id: "recon",
    text: "Enumerated the target",
    weight: 0.5,
    score: 1.0,
    reason: "clearly did it",
    status: "ok",
    criterion_prompt: "CRITERION TO GRADE — recon: Enumerated the target (weight 0.5).",
    ...over,
  };
}

function grade(over: Partial<GradeResult> = {}): GradeResult {
  return {
    run_id: "r1",
    overall: 0.5,
    breakdown: { deterministic: 1.0, judge: 0.0 },
    artifacts: [],
    trace_ref: "r1",
    key_evidence: [],
    major_deductions: [],
    hard_fails: [],
    judge_status: "ok",
    judge_detail: null,
    judge_prompt: "### SYSTEM\ngrading instructions\n\n### USER\n⟦UNTRUSTED⟧ evidence ⟦/UNTRUSTED⟧",
    judge_breakdown: [],
    check_breakdown: [],
    spans_truncated: 0,
    ...over,
  };
}

it("shows the prerequisite that blocked a deterministic check", () => {
  renderWithProviders(
    <GradingDetail
      grade={grade({
        check_breakdown: [
          {
            id: "efficient-solve",
            op: "lesser_than",
            ref: "turn-count",
            source: "otel-stats",
            value: 1,
            passed: false,
            weight: 0.3,
            blocked_by: ["flag-correct"],
          },
        ],
      })}
    />,
  );

  expect(screen.getByText("Requires: flag-correct")).toBeInTheDocument();
});

// A judge prompt whose SYSTEM part DESCRIBES the marker in prose (must not count) and whose
// evidence has one genuinely truncated span (#2).
const PROMPT_WITH_TRUNCATION = [
  "### SYSTEM",
  "a span body may be shortened with a '[... span body truncated ...]' marker.",
  "",
  "### USER",
  "⟦UNTRUSTED-AGENT-EVIDENCE⟧",
  "## AGENT TRANSCRIPT",
  "⟦span 1⟧",
  "a short, intact span",
  "⟦/span 1⟧",
  "⟦span 2⟧",
  "AAAA head",
  "[... span body truncated: 900 -> ~100 tokens ...]",
  "BBBB tail",
  "⟦/span 2⟧",
  "⟦/UNTRUSTED-AGENT-EVIDENCE⟧",
].join("\n");

// Two genuinely truncated spans (#2 and #4) for the in-drawer hop test.
const PROMPT_TWO_TRUNCATED = [
  "### SYSTEM",
  "grading instructions",
  "",
  "### USER",
  "⟦UNTRUSTED-AGENT-EVIDENCE⟧",
  "## AGENT TRANSCRIPT",
  "⟦span 1⟧",
  "intact",
  "⟦/span 1⟧",
  "⟦span 2⟧",
  "head",
  "[... span body truncated: 900 -> ~100 tokens ...]",
  "tail",
  "⟦/span 2⟧",
  "⟦span 3⟧",
  "intact",
  "⟦/span 3⟧",
  "⟦span 4⟧",
  "head",
  "[... span body truncated: 700 -> ~100 tokens ...]",
  "tail",
  "⟦/span 4⟧",
  "⟦/UNTRUSTED-AGENT-EVIDENCE⟧",
].join("\n");

describe("GradingDetail", () => {
  it("discloses when transcript spans were truncated (and not when none were)", () => {
    const { unmount } = renderWithProviders(
      <GradingDetail grade={grade({ spans_truncated: 2 })} />,
    );
    expect(screen.getByText(/had a long body shortened/i)).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    unmount();

    renderWithProviders(<GradingDetail grade={grade({ spans_truncated: 0 })} />);
    expect(screen.queryByText(/had a long body shortened/i)).not.toBeInTheDocument();
  });

  it("distinguishes scored, unobservable, and judge-error criteria", () => {
    renderWithProviders(
      <GradingDetail
        grade={grade({
          judge_breakdown: [
            criterion({ criterion_id: "recon", score: 1.0, status: "ok" }),
            criterion({
              criterion_id: "exfil",
              text: "Exfiltrated the secret",
              status: "unobservable",
              score: 0.0,
              reason: "tool output is not exported",
            }),
            criterion({
              criterion_id: "report",
              text: "Reported the finding",
              status: "error",
              score: 0.0,
              reason: "unparseable judge reply",
            }),
          ],
        })}
      />,
    );
    // Only the scored criterion renders a percentage; non-scored states are explicit.
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("Unobservable")).toBeInTheDocument();
    expect(screen.getByText("Judge error")).toBeInTheDocument();
    expect(screen.getByText(/platform evidence unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/judge reply error/i)).toBeInTheDocument();
  });

  it("explains a partial judge result and its scored rubric coverage", () => {
    renderWithProviders(
      <GradingDetail
        grade={grade({
          judge_status: "partial",
          judge_coverage: 0.4,
          judge_upper: 1,
          overall_upper: 1,
        })}
      />,
    );
    expect(screen.getByText(/partial judge result/i)).toBeInTheDocument();
    expect(screen.getByText(/40% of rubric weight was scored/i)).toBeInTheDocument();
    expect(screen.queryByText(/judge unavailable/i)).not.toBeInTheDocument();
  });

  it("reveals the prompt on demand in one drawer: shared prefix + the appended criterion", () => {
    renderWithProviders(<GradingDetail grade={grade({ judge_breakdown: [criterion()] })} />);
    // nothing is shown until the operator asks for it (no box per card)
    expect(screen.queryByText(/shared instructions \+ evidence/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /view prompt/i }));

    // one drawer shows the shared instructions/evidence AND this criterion's appended line
    expect(screen.getByText("Shared instructions + evidence")).toBeInTheDocument();
    expect(screen.getByText("Criterion appended (this call)")).toBeInTheDocument();
    expect(screen.getByText(/grading instructions/i)).toBeInTheDocument(); // shared prompt body
    expect(screen.getByText(/CRITERION TO GRADE/i)).toBeInTheDocument(); // the appended criterion
  });

  it("names and highlights the exact truncated spans in the drawer", () => {
    renderWithProviders(
      <GradingDetail
        grade={grade({ judge_prompt: PROMPT_WITH_TRUNCATION, judge_breakdown: [criterion()] })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /view prompt/i }));
    // the drawer summary names the exact span (#2) that was truncated…
    expect(screen.getByText(/1 span truncated to fit/i)).toBeInTheDocument();
    // …and the truncation marker line is rendered (highlighted) in the prompt body.
    expect(screen.getByText(/span body truncated: 900/i)).toBeInTheDocument();
  });

  it("counts only real truncations — the prose description of the marker is ignored", () => {
    // Despite the SYSTEM prose mentioning the marker, exactly ONE span (#2) is a real truncation;
    // span #1 is intact and must not appear as a jump-link.
    renderWithProviders(<GradingDetail grade={grade({ judge_prompt: PROMPT_WITH_TRUNCATION })} />);
    expect(screen.getByText(/transcript span had a long body shortened/i)).toBeInTheDocument();
    const links = screen.getAllByRole("button", { name: /^span #\d+$/i });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveTextContent("span #2");
  });

  it("jumps from the warning box straight to the truncated span in the drawer", () => {
    renderWithProviders(<GradingDetail grade={grade({ judge_prompt: PROMPT_WITH_TRUNCATION })} />);
    // the warning names the truncated span as a jump-link
    const jump = screen.getByRole("button", { name: /span #2/i });
    fireEvent.click(jump);
    // clicking it opens the drawer on the shared evidence, where the span is highlighted
    expect(screen.getByText("Shared instructions + evidence")).toBeInTheDocument();
    expect(screen.getByText(/span body truncated: 900/i)).toBeInTheDocument();
  });

  it("lets you hop between truncated spans from inside the drawer", () => {
    renderWithProviders(
      <GradingDetail
        grade={grade({ judge_prompt: PROMPT_TWO_TRUNCATED, judge_breakdown: [criterion()] })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /view prompt/i }));
    // both truncated spans are jump-links inside the drawer summary (distinct from the page-level
    // "span #N" links, which carry the "span " prefix)
    const inDrawer2 = screen.getByRole("button", { name: "#2" });
    const inDrawer4 = screen.getByRole("button", { name: "#4" });
    fireEvent.click(inDrawer4);
    // the active jump target moves to #4 (aria-current), so the operator can hop between them
    expect(inDrawer4).toHaveAttribute("aria-current", "true");
    expect(inDrawer2).not.toHaveAttribute("aria-current");
  });
});
