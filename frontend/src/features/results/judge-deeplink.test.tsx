import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { ResultsView } from "./results-view";

// ResultsView routes back to /runs after a delete — stub the app router.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// GET /runs/{id}/result returns RunResultView — the grade nests under `grade`,
// with conditions + partial state alongside it.
function result(judge_status: string) {
  return {
    grade: {
      run_id: "r1",
      overall: 0.5,
      breakdown: { deterministic: 0.5, judge: 0 },
      artifacts: [],
      trace_ref: "r1",
      judge_status,
      judge_detail: null,
      judge_breakdown: [],
      check_breakdown: [],
      hard_fails: [],
      key_evidence: [],
      major_deductions: [],
    },
    conditions: {
      model: null,
      judge_model: null,
      budget_seconds: 0,
      sandbox_ref: null,
      agent_version: 1,
      mission_version: 1,
    },
    partial: false,
    partial_trigger: null,
  };
}

describe("ResultsView judge deep-link", () => {
  it("links a model-not-configured result to Settings (focus=judge)", async () => {
    server.use(
      http.get("*/api/runs/r1/result", () =>
        HttpResponse.json(result("model-not-configured")),
      ),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    const link = await screen.findByRole("link", { name: /settings/i });
    expect(link.getAttribute("href")).toContain("focus=judge");
  });

  it("shows no judge warning when the judge ran ok", async () => {
    server.use(http.get("*/api/runs/r1/result", () => HttpResponse.json(result("ok"))));
    renderWithProviders(<ResultsView runId="r1" />);
    await screen.findByText("Result");
    expect(screen.queryByText(/not configured/i)).toBeNull();
  });
});
