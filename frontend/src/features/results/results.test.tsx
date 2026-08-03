import { describe, it, expect, vi } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture } from "@/test/fixtures";
import type { GradeResult, RunResultView } from "@/lib/api/types";

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

// ResultsView routes back to /runs after a delete — stub the app router.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { ResultsView } from "./results-view";

function grade(over: Partial<GradeResult> = {}): GradeResult {
  return {
    run_id: "r1",
    overall: 0.5,
    breakdown: { deterministic: 0.4, judge: 0.6 },
    artifacts: [],
    trace_ref: "r1",
    key_evidence: ["found the flag"],
    major_deductions: [],
    hard_fails: [],
    judge_status: "ok",
    judge_detail: null,
    judge_breakdown: [],
    check_breakdown: [
      {
        id: "flag-correct",
        op: "eq",
        ref: "flag",
        source: "control",
        value: "FLAG{x}",
        passed: true,
        weight: 1,
      },
    ],
    ...over,
  } as GradeResult;
}

// GET /runs/{id}/result now returns RunResultView: the grade nested under
// `grade`, with disclosed conditions + partial state alongside it.
function resultView(
  over: { grade?: Partial<GradeResult> } & Partial<
    Omit<RunResultView, "grade">
  > = {},
): RunResultView {
  const { grade: gradeOver, conditions, ...rest } = over;
  return {
    grade: grade(gradeOver),
    conditions: {
      model: "claude-3-5-sonnet",
      judge_model: "gpt-4o",
      budget_seconds: 600,
      sandbox_ref: "sbx-abc123",
      agent_version: 1,
      mission_version: 1,
      ...conditions,
    },
    partial: false,
    partial_trigger: null,
    ...rest,
  } as RunResultView;
}

describe("ResultsView", () => {
  it("renders the overall score, breakdown, evidence, checks and conditions", async () => {
    server.use(
      http.get("*/api/runs/r1/result", () => HttpResponse.json(resultView())),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    await waitFor(() => expect(screen.getByText("Overall")).toBeInTheDocument());
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText("found the flag")).toBeInTheDocument();
    expect(screen.getByText("flag-correct")).toBeInTheDocument();
    expect(screen.getByText("pass")).toBeInTheDocument();
    // Disclosed conditions render from r.conditions.
    expect(screen.getByText("Conditions")).toBeInTheDocument();
    expect(screen.getByText("claude-3-5-sonnet")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
    expect(screen.getByText("600s")).toBeInTheDocument();
  });

  it("titles the header with the run name and shows the mission in the meta bar", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({ run_id: "r1", mission: "sqli-login", name: "recon-run" }),
        ]),
      ),
      http.get("*/api/runs/r1/result", () => HttpResponse.json(resultView())),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    // The run name is the header title now; the mission (with its version) reads in the meta bar.
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "recon-run" }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("sqli-login v1")).toBeInTheDocument();
  });

  it("surfaces a clear note when the judge is unavailable", async () => {
    server.use(
      http.get("*/api/runs/r1/result", () =>
        HttpResponse.json(
          resultView({
            grade: {
              judge_status: "unavailable",
              judge_detail: "transport error",
            },
          }),
        ),
      ),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    await waitFor(() =>
      expect(screen.getByText(/Judge unavailable/i)).toBeInTheDocument(),
    );
  });

  it("shows a partial banner with the trigger when the run timed out", async () => {
    server.use(
      http.get("*/api/runs/r1/result", () =>
        HttpResponse.json(
          resultView({ partial: true, partial_trigger: "budget_exhausted" }),
        ),
      ),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    await waitFor(() =>
      expect(screen.getByText(/Partial — timed out/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("budget_exhausted")).toBeInTheDocument();
  });

  it("shows an operator-stopped partial banner, not a timeout one", async () => {
    // A manual (operator) termination is partial too, but it did NOT time out — the banner must
    // say it was stopped by the operator, not that it missed a deadline.
    server.use(
      http.get("*/api/runs/r1/result", () =>
        HttpResponse.json(
          resultView({ partial: true, partial_trigger: "operator" }),
        ),
      ),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    await waitFor(() =>
      expect(screen.getByText(/Partial — stopped early/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/terminated by the operator/i)).toBeInTheDocument();
    expect(screen.queryByText(/timed out/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/did not finish before its deadline/i),
    ).not.toBeInTheDocument();
  });

  it("shows the trace ref and the submitted artifacts with their content", async () => {
    server.use(
      http.get("*/api/runs/r1/result", () =>
        HttpResponse.json(
          resultView({
            grade: {
              trace_ref: "trace-r1-abc",
              artifacts: ["report.md", "flag"],
            },
          }),
        ),
      ),
      http.get("*/api/runs/r1/artifacts", () =>
        HttpResponse.json([
          { name: "report.md", kind: "artifact", seq: 1, payload: "## Recon\nfound an IDOR" },
          { name: "flag", kind: "flag", seq: 2, payload: "XORCISE{demo-flag}" },
        ]),
      ),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    await waitFor(() =>
      expect(screen.getByText(/trace ref: trace-r1-abc/i)).toBeInTheDocument(),
    );
    // Names AND the actual submitted content are reviewable — not just thin refs.
    // The artifacts fetch is a separate request, so await its content.
    expect(await screen.findByText("Artifacts")).toBeInTheDocument();
    expect(screen.getByText("report.md")).toBeInTheDocument();
    expect(screen.getByText(/found an IDOR/)).toBeInTheDocument();
    expect(screen.getByText("XORCISE{demo-flag}")).toBeInTheDocument();
  });

  it("preserves and shows the exact prompt fed to the LLM judge", async () => {
    const judgePrompt =
      "RUBRIC (evaluation steps):\n- r1: did it (weight 1.0)\n\nAGENT TRANSCRIPT:\nspan-one did it";
    server.use(
      http.get("*/api/runs/r1/result", () =>
        HttpResponse.json(resultView({ grade: { judge_prompt: judgePrompt } })),
      ),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    await waitFor(() => expect(screen.getByText("Overall")).toBeInTheDocument());
    // The prompt is surfaced on demand in a single drawer, not inline on the page.
    const open = screen.getByRole("button", { name: /view the judge prompt/i });
    fireEvent.click(open);
    expect(screen.getByText(/span-one did it/)).toBeInTheDocument();
  });

  it("omits the judge-prompt block when no prompt was preserved", async () => {
    server.use(
      http.get("*/api/runs/r1/result", () =>
        HttpResponse.json(resultView({ grade: { judge_prompt: null } })),
      ),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    await waitFor(() => expect(screen.getByText("Overall")).toBeInTheDocument());
    expect(screen.queryByText(/Judge prompt/i)).not.toBeInTheDocument();
  });

  it("renders a not-found state on a 404", async () => {
    server.use(
      http.get("*/api/runs/ghost/result", () =>
        HttpResponse.json({ detail: "no run" }, { status: 404 }),
      ),
    );
    renderWithProviders(<ResultsView runId="ghost" />);
    await waitFor(() =>
      expect(screen.getByText("Result not found")).toBeInTheDocument(),
    );
  });

  // ═══ terminal-but-ungraded run must not crash ═══
  // Grading runs async after the run goes terminal, so the result endpoint has
  // no grade yet. It returns 202 {status:"grading"}, which api.get
  // surfaces as a gradeless body. The view must render a graceful state instead
  // of throwing on r.overall.

  it("renders a graceful grading state when the result body has no grade", async () => {
    // RunResultView shape minus `grade` — the ticket's stated repro.
    server.use(
      http.get("*/api/runs/r1/result", () =>
        HttpResponse.json({
          run_id: "r1",
          conditions: {
            model: "claude-3-5-sonnet",
            judge_model: null,
            budget_seconds: 600,
            sandbox_ref: null,
            agent_version: null,
            mission_version: null,
          },
          partial: false,
          partial_trigger: null,
        }),
      ),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    await waitFor(() =>
      expect(screen.getByText(/grading in progress/i)).toBeInTheDocument(),
    );
    // The grade UI (scorecard) must not render when there is no grade.
    expect(screen.queryByText("Overall")).not.toBeInTheDocument();
  });

  it("renders the grading state on a real 202 grading response", async () => {
    server.use(
      http.get("*/api/runs/r1/result", () =>
        HttpResponse.json({ run_id: "r1", status: "grading" }, { status: 202 }),
      ),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    await waitFor(() =>
      expect(screen.getByText(/grading in progress/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Overall")).not.toBeInTheDocument();
  });

  // ═══ Re-evaluate (re-grade the saved evidence) ═══

  it("re-evaluates a run: POSTs regrade and flips to the re-evaluating state", async () => {
    let regraded = false;
    let graded = true; // first load is graded (judge failed); after regrade the result 202s
    server.use(
      http.get("*/api/runs/r1/result", () =>
        graded
          ? HttpResponse.json(
              resultView({
                grade: grade({
                  judge_status: "unavailable",
                  judge_detail: "judge evidence too large",
                }),
              }),
            )
          : HttpResponse.json({ run_id: "r1", status: "grading" }, { status: 202 }),
      ),
      http.post("*/api/runs/r1/regrade", () => {
        regraded = true;
        graded = false; // the re-grade dropped the result; subsequent polls are 202
        return HttpResponse.json({ run_id: "r1", status: "grading" }, { status: 202 });
      }),
    );
    renderWithProviders(<ResultsView runId="r1" />);

    // The graded result renders with a Re-evaluate action.
    fireEvent.click(await screen.findByRole("button", { name: /re-evaluate/i }));

    await waitFor(() => expect(regraded).toBe(true));
    // The page reads as re-evaluating (the invalidated result now 202s) — not a first-time grade.
    await waitFor(() =>
      expect(screen.getByText(/re-evaluating/i)).toBeInTheDocument(),
    );
  });

  // ═══ "Grading stuck forever" fix ═══

  it(
    "polls while grading and resolves to the scorecard once the grade lands",
    { timeout: 10_000 },
    async () => {
      // First request 202s (grading still running server-side); the poll's refetch then finds
      // the recorded result. Before the fix the spinner never resolved without a manual reload.
      let calls = 0;
      server.use(
        http.get("*/api/runs/r1/result", () => {
          calls += 1;
          return calls === 1
            ? HttpResponse.json(
                { run_id: "r1", status: "grading" },
                { status: 202 },
              )
            : HttpResponse.json(resultView());
        }),
      );
      renderWithProviders(<ResultsView runId="r1" />);
      await waitFor(() =>
        expect(screen.getByText(/grading in progress/i)).toBeInTheDocument(),
      );
      // The poll interval is 3s — wait past it for the refetched, graded body.
      await waitFor(
        () => expect(screen.getByText("Overall")).toBeInTheDocument(),
        { timeout: 8_000 },
      );
      expect(screen.queryByText(/grading in progress/i)).not.toBeInTheDocument();
    },
  );

  it("shows why a check could not execute (defensive-grading error note)", async () => {
    server.use(
      http.get("*/api/runs/r1/result", () =>
        HttpResponse.json(
          resultView({
            grade: {
              // `error` is additive server-side; the generated schema lags until the central
              // codegen refresh, hence the cast.
              check_breakdown: [
                {
                  id: "legacy",
                  op: "regex",
                  ref: "flag",
                  source: "artifacts",
                  value: null,
                  passed: false,
                  weight: 1,
                  error:
                    "unknown op 'regex' (known: equals, lesser_than, matches_format, observed)",
                },
              ] as unknown as GradeResult["check_breakdown"],
            },
          }),
        ),
      ),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    await waitFor(() => expect(screen.getByText("legacy")).toBeInTheDocument());
    expect(screen.getByText("fail")).toBeInTheDocument();
    expect(screen.getByText(/unknown op 'regex'/)).toBeInTheDocument();
  });

  it("offers the run report as a .md and .html download in the header", async () => {
    server.use(
      http.get("*/api/runs/r1/result", () => HttpResponse.json(resultView())),
    );
    renderWithProviders(<ResultsView runId="r1" />);
    // Plain anchors straight at the server — Content-Disposition names the file.
    const md = await screen.findByRole("link", { name: /Report \(\.md\)/ });
    expect(md).toHaveAttribute(
      "href",
      `${window.location.origin}/api/runs/r1/report?format=md`,
    );
    expect(
      screen.getByRole("link", { name: /Report \(\.html\)/ }),
    ).toHaveAttribute(
      "href",
      `${window.location.origin}/api/runs/r1/report?format=html`,
    );
  });
});
