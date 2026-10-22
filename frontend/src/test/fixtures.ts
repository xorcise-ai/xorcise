import type {
  AgentEntry,
  CatalogEntry,
  RunEntry,
  RunScore,
  RunStatus,
  ScoreSummary,
} from "@/lib/api/types";
// The server's classification and status wording for every (state, trigger), generated from
// contracts/run.py and checked against it by tests/unit/test_run_status.py — so a fixture run
// carries exactly what the server would send, and no test restates the mapping (#165).
import golden from "./run-status.golden.json";

type ServerView = { outcome: RunEntry["outcome"]; status: RunStatus };
const GOLDEN = golden as Record<string, ServerView>;

/** What the server derives for a run in `state`, ended by `trigger`. A state the server never
 *  writes (some tests use "running") falls back like the server does: its own name, muted. */
export function serverView(
  state: string,
  trigger: RunEntry["terminal_trigger"] = null,
): ServerView {
  return (
    GOLDEN[`${state}/${trigger ?? ""}`] ?? {
      outcome: null,
      status: { label: state, tone: "muted", detail: null },
    }
  );
}

export const agentFixture = (over: Partial<AgentEntry> = {}): AgentEntry => ({
  id: "agent-1",
  name: "scout",
  endpoint: "http://localhost:9000",
  otel: null,
  kind: "generic", // what the server stores for a declaration that names no harness
  version: 1,
  created_at: "2026-06-29T10:00:00Z",
  ...over,
});

export const runFixture = (over: Partial<RunEntry> = {}): RunEntry => {
  const base = {
    run_id: "run-1",
    agent_id: "agent-1",
    agent_version: 1,
    mission: "sqli-login",
    install_revision: 1,
    // Neutral default that contains no mission slug, so tests that query cards by their mission
    // (e.g. /sqli-login/i) resolve via the mission fact, not this label. Tests that assert on the
    // title set an explicit name.
    name: "test run",
    state: "running",
    created_at: "2026-06-29T10:00:00Z",
    completed_at: null,
    terminal_trigger: null,
    budget_seconds: 600,
    source_agent: "generic",
    intel_policy: "all",
    ...over,
  };
  // Derived by the server from state + trigger — filled in the same way unless a test overrides.
  return { ...serverView(base.state, base.terminal_trigger), ...base } as RunEntry;
};

export const missionFixture = (
  over: Partial<CatalogEntry> = {},
): CatalogEntry => ({
  mission_id: "sqli-login",
  name: "SQLi Login Bypass",
  summary: "Bypass a login form via SQL injection.",
  source: "your_own",
  installed: true,
  image: null,
  proficiency: "beginner",
  specialty: "web",
  type: "ctf",
  skills: [],
  technologies: [],
  platforms: [],
  ...over,
});

/** One finished run as GET /results/runs serves it. Defaults to a scored run: deterministic 1.0,
 *  judge 0.8, overall 0.9. The `bucket` is the server's to decide, so a test sets it with the
 *  fields that make it true rather than having this fixture guess. */
export const scoreFixture = (over: Partial<RunScore> = {}): RunScore => ({
  run_id: "r1",
  agent_id: "agent-1",
  mission: "sqli-login",
  mission_version: "1.0.0",
  current_version: true,
  created_at: "2026-06-29T10:00:00Z",
  completed_at: "2026-06-29T10:05:00Z",
  outcome: "completed",
  terminal_trigger: "done",
  bucket: "scored",
  deterministic: 1,
  judge: 0.8,
  judge_upper: null,
  judge_status: "ok",
  overall: 0.9,
  overall_upper: null,
  judge_model: "judge-m",
  ...over,
});

/** A GET /results/summary group. Defaults to one scored run at 0.9. */
export const summaryFixture = (over: Partial<ScoreSummary> = {}): ScoreSummary => ({
  agent_id: null,
  mission: null,
  mission_version: null,
  current_version: null,
  runs: 1,
  completed: 1,
  scored: 1,
  partial_judge: 0,
  no_judge: 0,
  ended_early: 0,
  infra_failed: 0,
  grading: 0,
  median: 0.9,
  q1: 0.9,
  q3: 0.9,
  best: 0.9,
  judge_gap: -0.2,
  judge_models: ["judge-m"],
  last_run: "2026-06-29T10:05:00Z",
  ...over,
});
