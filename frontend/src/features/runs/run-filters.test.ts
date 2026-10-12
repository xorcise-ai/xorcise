import { describe, it, expect } from "vitest";
import { distinctStatuses } from "./run-filters";
import { runFixture } from "@/test/fixtures";

// #165: the Status facet orders by the server's state and outcome, not by a list of label strings
// — that list went stale the moment the labels moved to the server, sending every terminal status
// but "Completed" to the alphabetical tail.
describe("distinctStatuses", () => {
  it("puts live runs first, then completed, partial, infra failures and unknown endings", () => {
    const runs = [
      runFixture({ run_id: "a", state: "terminal", terminal_trigger: "crashed" }),
      runFixture({ run_id: "b", state: "terminal", terminal_trigger: "timeout" }),
      runFixture({ run_id: "c", state: "terminal", terminal_trigger: null }),
      runFixture({ run_id: "d", state: "terminal", terminal_trigger: "done" }),
      runFixture({ run_id: "e", state: "terminal", terminal_trigger: "operator" }),
      runFixture({ run_id: "f", state: "created" }),
      runFixture({ run_id: "g", state: "terminal", terminal_trigger: "deploy_failed" }),
      runFixture({ run_id: "h", state: "active" }),
      runFixture({ run_id: "i", state: "terminal", terminal_trigger: "done" }),
    ];
    expect(distinctStatuses(runs)).toEqual([
      "Running",
      "Created",
      "Completed",
      "Stopped",
      "Timed out",
      "Crashed",
      "Deploy failed",
      "Ended",
    ]);
  });
});
