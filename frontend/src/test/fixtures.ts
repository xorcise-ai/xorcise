import type {
  AgentEntry,
  CatalogEntry,
  RunEntry,
} from "@/lib/api/types";

export const agentFixture = (over: Partial<AgentEntry> = {}): AgentEntry => ({
  id: "agent-1",
  name: "scout",
  endpoint: "http://localhost:9000",
  otel: null,
  version: 1,
  created_at: "2026-06-29T10:00:00Z",
  ...over,
});

export const runFixture = (over: Partial<RunEntry> = {}): RunEntry => ({
  run_id: "run-1",
  agent_id: "agent-1",
  agent_version: 1,
  mission: "sqli-login",
  mission_version: 1,
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
});

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
