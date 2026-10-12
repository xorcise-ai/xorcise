import type {
  AgentEntry,
  CatalogEntry,
  RunEntry,
  RunStatus,
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
