import { describe, it, expect } from "vitest";
import { runPhase, toneColor } from "./run-phase";

const run = (state: string, trigger: string | null = null) =>
  ({ state, terminal_trigger: trigger }) as Parameters<typeof runPhase>[0];

describe("runPhase", () => {
  it("created with no trace → awaiting agent (amber, active)", () => {
    const p = runPhase(run("created"), 0);
    expect(p.key).toBe("awaiting-agent");
    expect(p.tone).toBe("amber");
    expect(p.active).toBe(true);
    expect(p.helpText).toMatch(/OpenTelemetry|trace/i);
  });

  it("active but no trace yet → connecting (amber, active)", () => {
    const p = runPhase(run("active"), 0);
    expect(p.key).toBe("connecting");
    expect(p.tone).toBe("amber");
    expect(p.active).toBe(true);
  });

  it("trace flowing and not terminal → streaming (green, active)", () => {
    const p = runPhase(run("active"), 5);
    expect(p.key).toBe("streaming");
    expect(p.tone).toBe("green");
    expect(p.active).toBe(true);
  });

  it("terminal done → solved (green, inactive)", () => {
    const p = runPhase(run("terminal", "done"), 12);
    expect(p.key).toBe("solved");
    expect(p.tone).toBe("green");
    expect(p.active).toBe(false);
  });

  it.each(["error", "timeout", "budget"])(
    "terminal %s → failed (red)",
    (trigger) => {
      const p = runPhase(run("terminal", trigger), 3);
      expect(p.key).toBe("failed");
      expect(p.tone).toBe("red");
      expect(p.label).toBe(trigger);
    },
  );

  it("terminal via operator → ended (muted)", () => {
    const p = runPhase(run("terminal", "operator"), 1);
    expect(p.key).toBe("ended");
    expect(p.tone).toBe("muted");
  });

  it("toneColor maps to CSS vars", () => {
    expect(toneColor("green")).toContain("--color-ok");
    expect(toneColor("red")).toContain("--color-err");
    expect(toneColor("amber")).toContain("--color-primary");
  });
});

describe("runPhase stall awareness", () => {
  it("flags a silent, previously-streaming run as stalled (red, inactive)", () => {
    const p = runPhase(run("active"), 5, undefined, 240);
    expect(p.key).toBe("stalled");
    expect(p.label).toBe("Stalled");
    expect(p.tone).toBe("red");
    expect(p.active).toBe(false);
    expect(p.helpText).toMatch(/4 min/);
  });

  it("never stalls a run that has not emitted at all — that state is Connecting/Awaiting", () => {
    expect(runPhase(run("active"), 0, undefined, 240).key).toBe("connecting");
  });

  it("a dead environment still outranks a stall", () => {
    expect(runPhase(run("active"), 5, "failed", 240).key).toBe("environment-failed");
  });

  it("a terminal run reports its outcome, never a stall", () => {
    expect(runPhase(run("terminal", "done"), 5, undefined, 240).key).toBe("solved");
  });

  it("keeps streaming while not stalled", () => {
    expect(runPhase(run("active"), 5, undefined, null).key).toBe("streaming");
  });
});

describe("runPhase environment awareness", () => {
  it("says the environment is starting rather than blaming the agent", () => {
    // The operator saw "Awaiting agent" next to "Environment: Starting" — which reads as "your
    // agent is late" and invites launching it against targets that do not exist yet.
    const p = runPhase(run("created"), 0, "starting");
    expect(p.key).toBe("provisioning");
    expect(p.label).toBe("Starting environment");
    expect(p.tone).toBe("amber");
    expect(p.active).toBe(true);
    expect(p.helpText).toMatch(/environment/i);
  });

  it("awaits the agent once the environment is ready", () => {
    expect(runPhase(run("created"), 0, "ready").key).toBe("awaiting-agent");
  });

  it("awaits the agent immediately for a static mission", () => {
    // A static (attachment-only) mission has NO environment to come up, so waiting on one would
    // be waiting forever — the agent really is the next step.
    expect(runPhase(run("created"), 0, "none").key).toBe("awaiting-agent");
  });

  it("surfaces a dead environment in red, ahead of any agent chatter", () => {
    // A run whose environment died is about to be closed out; saying "Streaming" because the agent
    // is still emitting into the void would hide the actual fault.
    const p = runPhase(run("created"), 5, "failed");
    expect(p.key).toBe("environment-failed");
    expect(p.tone).toBe("red");
  });

  it("keeps streaming once the agent emits while the environment finishes coming up", () => {
    expect(runPhase(run("created"), 3, "starting").key).toBe("streaming");
  });

  it("is unchanged when no environment state is known", () => {
    expect(runPhase(run("created"), 0).key).toBe("awaiting-agent");
  });

  it("a terminal run still reports its own outcome, not the environment", () => {
    expect(runPhase(run("terminal", "done"), 3, "released").key).toBe("solved");
  });
});
