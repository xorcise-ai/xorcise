import { describe, it, expect } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { createQueryWrapper } from "@/test/render";
import { environmentPollInterval, useRunEnvironment } from "./queries";
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

describe("useRunEnvironment", () => {
  it("keeps polling a run that finishes while its environment still reads ready", async () => {
    // Review of #185, at the hook level: the run query sees the run finish before the
    // environment poll does, so the hook flips to inactive with `ready` still cached. It used to
    // stop there, and a failed teardown never reached the run page.
    let state: RunEnvironment["state"] = "ready";
    let requests = 0;
    server.use(
      http.get("*/api/runs/r1/environment", () => {
        requests += 1;
        return HttpResponse.json({
          run_id: "r1",
          state,
          ready: state === "ready",
          detail: state === "releasing" ? "teardown failed 1 time(s), retrying automatically" : "",
          release_attempts: state === "releasing" ? 1 : 0,
        });
      }),
    );
    const { result, rerender } = renderHook(({ active }) => useRunEnvironment("r1", active), {
      wrapper: createQueryWrapper(),
      initialProps: { active: true },
    });
    await waitFor(() => expect(result.current.data?.state).toBe("ready"));

    state = "releasing";
    rerender({ active: false }); // the run just finished; `ready` is still cached
    const before = requests;
    await waitFor(() => expect(result.current.data?.state).toBe("releasing"), {
      timeout: 4000,
    });
    expect(requests).toBeGreaterThan(before);
    expect(result.current.data?.release_attempts).toBe(1);
  });
});
