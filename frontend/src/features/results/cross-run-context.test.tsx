import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture, scoreFixture, summaryFixture } from "@/test/fixtures";
import { CrossRunContext } from "./cross-run-context";

const run = runFixture({ run_id: "r9", agent_id: "agent-1", mission: "sqli-login", mission_version: "2.0.0" });

// The run's own row, as the server labels its version: the card reads the version from here.
const OWN = scoreFixture({ run_id: "r9", mission_version: "2.0.0" });

function serve(summaries: ReturnType<typeof summaryFixture>[], scores = [OWN]) {
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

  it("does not call a run the first when the runs before it were left out of the median", async () => {
    serve([
      summaryFixture({ agent_id: "agent-1", mission: "sqli-login", mission_version: "2.0.0", runs: 10, scored: 1, stopped: 9 }),
    ]);
    renderWithProviders(<CrossRunContext run={run} thisOverall={0.8} />);
    expect(
      await screen.findByText("No other scored run for this agent on this mission to compare against yet."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/First run/)).toBeNull();
  });

  it("does not measure a run left out of the median against it", async () => {
    serve(
      [summaryFixture({ agent_id: "agent-1", mission: "sqli-login", mission_version: "2.0.0", runs: 4, scored: 3, median: 0.6, no_judge: 1 })],
      [scoreFixture({ run_id: "r9", mission_version: "2.0.0", bucket: "no_judge" })],
    );
    renderWithProviders(<CrossRunContext run={run} thisOverall={0.4} />);
    expect(await screen.findByText("not in the median")).toBeInTheDocument();
    expect(screen.queryByText(/pts vs median/)).toBeNull();
    expect(screen.getByText("Median (n=3)")).toBeInTheDocument();
  });

  it("takes the version from the server's row for this run, not from the run itself", async () => {
    // A pre-contract run carries no SemVer; the server labels it by its install revision.
    serve(
      [summaryFixture({ agent_id: "agent-1", mission: "sqli-login", mission_version: "7", median: 0.5, scored: 4 })],
      [scoreFixture({ run_id: "r9", mission_version: "7" })],
    );
    renderWithProviders(
      <CrossRunContext run={{ ...run, mission_version: null, install_revision: 7 }} thisOverall={0.8} />,
    );
    expect(await screen.findByText("+30 pts vs median")).toBeInTheDocument();
  });

  it("shows nothing until the server answers, never a premature first run", async () => {
    let answer: () => void = () => {};
    const held = new Promise<void>((resolve) => (answer = resolve));
    server.use(
      http.get("*/api/results/summary", async () => {
        await held;
        return HttpResponse.json([
          summaryFixture({ agent_id: "agent-1", mission: "sqli-login", mission_version: "2.0.0", median: 0.6, scored: 3 }),
        ]);
      }),
      http.get("*/api/results/runs", () => HttpResponse.json([OWN])),
    );
    const { container } = renderWithProviders(<CrossRunContext run={run} thisOverall={0.8} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/First run/)).toBeNull();
    answer();
    await waitFor(() => expect(screen.getByText("+20 pts vs median")).toBeInTheDocument());
  });
});
