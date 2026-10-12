import { describe, it, expect } from "vitest";
import { resultPollInterval } from "./queries";
import type { RunResultView } from "@/lib/api/types";

// The "grading stuck forever" fix (frontend half): the result query polls while the body is
// gradeless (the 202 {status:"grading"} shape) so the spinner resolves once grading records a
// result — and stops polling the moment the grade lands or when there is no body at all.
describe("resultPollInterval", () => {
  it("polls while the body is gradeless (202 grading)", () => {
    const grading = { run_id: "r1", status: "grading" } as unknown as RunResultView;
    expect(resultPollInterval(grading)).toBe(3000);
  });

  it("stops once the grade lands", () => {
    const graded = { grade: { run_id: "r1" } } as unknown as RunResultView;
    expect(resultPollInterval(graded)).toBe(false);
  });

  it("stops on a final not_graded answer (an environment failure is never graded)", () => {
    const notGraded = {
      run_id: "r1",
      status: "not_graded",
      terminal_trigger: "deploy_failed",
      detail: null,
    } as unknown as RunResultView;
    expect(resultPollInterval(notGraded)).toBe(false);
  });

  it("stops when there is no body (error path — a 404 stays a hard stop)", () => {
    expect(resultPollInterval(undefined)).toBe(false);
  });
});
