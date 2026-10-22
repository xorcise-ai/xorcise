import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture, scoreFixture, summaryFixture } from "@/test/fixtures";
import { CrossRunContext } from "./cross-run-context";

const run = runFixture({ run_id: "r9", agent_id: "agent-1", mission: "sqli-login", mission_version: "2.0.0" });

function serve(summaries: ReturnType<typeof summaryFixture>[], scores = [scoreFixture()]) {
  server.use(
    http.get("*/api/results/summary", () => HttpResponse.json(summaries)),
    http.get("*/api/results/runs", () => HttpResponse.json(scores)),
  );
}

// This run against the same agent's runs on the same mission VERSION, by the server's summary.
describe("CrossRunContext", () => {
  it("compares the run with the median of the agent's scored runs on this version", async () => {
    serve([
      summaryFixture({ agent_id: "agent-1", mission: "sqli-login", mission_version: "1.0.0", median: 0.2, scored: 5 }),
      summaryFixture({ agent_id: "agent-1", mission: "sqli-login", mission_version: "2.0.0", median: 0.6, scored: 3, best: 0.9 }),
    ]);
    renderWithProviders(<CrossRunContext run={run} thisOverall={0.8} />);
    expect(await screen.findByText("+20 pts vs median")).toBeInTheDocument();
    expect(screen.getByText("Median (n=3)")).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();
  });

  it("names the runs left out of the median", async () => {
    serve([
      summaryFixture({ agent_id: "agent-1", mission: "sqli-login", mission_version: "2.0.0", scored: 2, no_judge: 1, stopped: 1 }),
    ]);
    renderWithProviders(<CrossRunContext run={run} thisOverall={0.8} />);
    expect(await screen.findByText("1 no judge · 1 stopped: not in the median.")).toBeInTheDocument();
  });

  it("says when there is nothing to compare against yet", async () => {
    serve([summaryFixture({ agent_id: "agent-1", mission: "sqli-login", mission_version: "2.0.0", scored: 1 })]);
    renderWithProviders(<CrossRunContext run={run} thisOverall={0.8} />);
    expect(await screen.findByText(/First run for this agent on this mission/)).toBeInTheDocument();
  });
});
