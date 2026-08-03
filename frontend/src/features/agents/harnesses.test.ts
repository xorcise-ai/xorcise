import { describe, it, expect } from "vitest";
import { HARNESSES } from "./harnesses";

describe("HARNESSES", () => {
  it("offers Codex as a selectable harness", () => {
    const codex = HARNESSES.find((h) => h.kind === "codex");
    expect(codex).toBeDefined();
    expect(codex?.name).toBe("Codex CLI");
    // kind must match the run's source_agent / provider name so the launch + telemetry providers
    // (and, later, the replay adapter) select for it.
    expect(codex?.kind).toBe("codex");
  });

  it("names the CLI harnesses after the CLI, without renaming their kind slugs", () => {
    // The display name says which artefact is evaluated (the coding CLI); the slug is a
    // contract value — agent.kind → run.source_agent → adapter selection — so it stays put.
    const claude = HARNESSES.find((h) => h.kind === "claude-code");
    expect(claude?.name).toBe("Claude Code CLI");
    expect(claude?.kind).toBe("claude-code");
    // OpenHands is a product name, not a CLI brand — left alone.
    expect(HARNESSES.find((h) => h.kind === "openhands")?.name).toBe("OpenHands");
  });

  it("marks every shipped harness live (each has a rich replay adapter)", () => {
    // Codex went live when its replay adapter (harness_adapters/codex/otel.py) landed in Phase 2.
    for (const kind of ["openhands", "claude-code", "codex"]) {
      expect(HARNESSES.find((h) => h.kind === kind)?.live).toBe(true);
    }
  });
});
