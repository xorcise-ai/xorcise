import { describe, it, expect } from "vitest";
import { environmentPollInterval } from "./queries";
import type { RunEnvironment } from "@/lib/api/types";

const env = (state: RunEnvironment["state"]): RunEnvironment => ({
  run_id: "r1",
  state,
  ready: false,
  detail: "",
  release_attempts: 0,
});

describe("environmentPollInterval", () => {
  it("polls a live run's environment", () => {
    expect(environmentPollInterval(true, undefined)).toBe(2000);
  });

  it("keeps polling a finished run until its environment is released", () => {
    // #173: the chip must move from Releasing to Released (or Teardown failed) without a reload.
    expect(environmentPollInterval(false, env("releasing"))).toBe(10_000);
  });

  it("keeps polling a run that just finished while its last state is still live", () => {
    // The chip froze at Ready: the state read while the run was live is not "releasing", and
    // stopping on it meant the finished run never showed Releasing or Released.
    expect(environmentPollInterval(false, env("ready"))).toBe(2000);
    expect(environmentPollInterval(false, env("starting"))).toBe(2000);
    expect(environmentPollInterval(false, env("failed"))).toBe(2000);
  });

  it("stops once a finished run's environment is released", () => {
    expect(environmentPollInterval(false, env("released"))).toBe(false);
    expect(environmentPollInterval(false, env("none"))).toBe(false);
    expect(environmentPollInterval(false, undefined)).toBe(false);
  });
});
