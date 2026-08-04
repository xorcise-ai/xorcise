import { describe, it, expect } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { createQueryWrapper } from "@/test/render";
import type { AgentEvent, RunEventsView } from "@/lib/api/types";
import { useRunEvents } from "./use-run-events";

function agentEvent(seq: number, runId: string): AgentEvent {
  return {
    run_id: runId,
    id: `evt-${seq}`,
    ts: new Date(seq * 1000).toISOString(),
    source_agent: "generic",
    kind: "terminal_command",
    role: "agent",
    title: `cmd-${seq}`,
    body: `echo ${seq}`,
    data: {},
    severity: "info",
    raw_ref: { run_id: runId, raw_seq: seq, span_id: `s${seq}`, signal: "trace" },
  };
}

function eventsView(
  runId: string,
  events: AgentEvent[],
  nextSince: number,
  nextLogSince = nextSince,
): RunEventsView {
  return {
    run_id: runId,
    source_agent: "openhands",
    adapter_name: "openhands",
    adapter_version: "1",
    fallback: false,
    next_since: nextSince,
    next_cursor: { trace_seq: nextSince, log_seq: nextLogSince },
    counts: {},
    warnings: [],
    events,
  };
}

describe("useRunEvents", () => {
  it("loads the initial page for a terminal run (single fetch) and exposes meta", async () => {
    server.use(
      http.get("*/api/runs/r1/events", () =>
        HttpResponse.json(eventsView("r1", [agentEvent(1, "r1"), agentEvent(2, "r1")], 2)),
      ),
    );
    const { result } = renderHook(() => useRunEvents("r1", false), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.events[0].title).toBe("cmd-1");
    expect(result.current.meta).toEqual({
      sourceAgent: "openhands",
      adapterName: "openhands",
      adapterVersion: "1",
      fallback: false,
    });
  });

  it("accumulates new events across polls using the since cursor, deduping by id", async () => {
    server.use(
      http.get("*/api/runs/r2/events", ({ request }) => {
        const since = Number(new URL(request.url).searchParams.get("since"));
        const all = [agentEvent(1, "r2"), agentEvent(2, "r2"), agentEvent(3, "r2")];
        const fresh = all.filter((e) => e.raw_ref.raw_seq > since);
        const maxSeq = all.length ? Math.max(...all.map((e) => e.raw_ref.raw_seq)) : since;
        return HttpResponse.json(eventsView("r2", fresh, maxSeq));
      }),
    );
    const { result } = renderHook(() => useRunEvents("r2", true), {
      wrapper: createQueryWrapper(),
    });
    // first poll (since=-1) → all 3; later polls (since=3) → none, settling at 3.
    await waitFor(() => expect(result.current.events).toHaveLength(3), {
      timeout: 4000,
    });
    // no duplicates despite repeated polling
    const ids = result.current.events.map((e) => e.id);
    expect(new Set(ids).size).toBe(3);
  });

  it("dedupes an already-seen event re-returned by the server (ignores ?since)", async () => {
    // Ignore the `since` query param entirely and never advance next_since, so
    // the server re-includes the same already-seen event on every poll. This
    // genuinely exercises the seenRef dedup filter (the previous version of
    // this test filtered by `since` server-side, so a duplicate could never
    // arrive and the dedup branch went untested).
    let pollCount = 0;
    server.use(
      http.get("*/api/runs/r3/events", () => {
        pollCount += 1;
        return HttpResponse.json(eventsView("r3", [agentEvent(1, "r3")], 0));
      }),
    );
    const { result } = renderHook(() => useRunEvents("r3", true), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.events).toHaveLength(1));
    // Wait for at least one more poll to land (refetchInterval is 1500ms) so
    // the duplicate event actually gets re-fetched, then assert dedup held.
    await waitFor(() => expect(pollCount).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].id).toBe("evt-1");
  });

  it("does not let an advanced trace cursor hide a later log event with the same seq", async () => {
    let poll = 0;
    server.use(
      http.get("*/api/runs/r4/events", ({ request }) => {
        const url = new URL(request.url);
        const traceSince = Number(url.searchParams.get("trace_since"));
        const logSince = Number(url.searchParams.get("log_since"));
        poll += 1;
        if (poll === 1) {
          expect(traceSince).toBe(-1);
          expect(logSince).toBe(-1);
          return HttpResponse.json(eventsView("r4", [agentEvent(0, "r4")], 0, -1));
        }
        expect(traceSince).toBe(0);
        expect(logSince).toBe(-1);
        const assistant = {
          ...agentEvent(0, "r4"),
          id: "log-0",
          kind: "message" as const,
          role: "agent" as const,
          title: "assistant message",
          body: "done",
          raw_ref: {
            run_id: "r4",
            raw_seq: 0,
            span_id: "",
            signal: "log" as const,
          },
        };
        return HttpResponse.json(eventsView("r4", [assistant], 0, 0));
      }),
    );
    const { result } = renderHook(() => useRunEvents("r4", true), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.events).toHaveLength(2), {
      timeout: 4000,
    });
    expect(result.current.events.map((event) => event.id)).toHaveLength(2);
    expect(result.current.events.map((event) => event.id)).toEqual(
      expect.arrayContaining(["evt-0", "log-0"]),
    );
  });

  it("seeds the stall clock from the backlog's newest receipt time, clamped to now", async () => {
    // Reloading the page for an already-quiet run must NOT reset its stall clock: the first
    // batch is backlog replay, so the mark comes from received_at, not from the poll's arrival.
    const received = "2026-01-01T00:00:00.000Z";
    server.use(
      http.get("*/api/runs/r5/events", () =>
        HttpResponse.json(
          eventsView(
            "r5",
            [
              { ...agentEvent(1, "r5"), received_at: "2025-12-31T23:59:00.000Z" },
              { ...agentEvent(2, "r5"), received_at: received },
            ],
            2,
          ),
        ),
      ),
    );
    const { result } = renderHook(() => useRunEvents("r5", false), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.lastEventAt).toBe(Date.parse(received));
  });

  it("stamps later fresh batches with the client clock, not the receipt time", async () => {
    const testStart = Date.now();
    let poll = 0;
    server.use(
      http.get("*/api/runs/r6/events", () => {
        poll += 1;
        if (poll === 1)
          return HttpResponse.json(
            eventsView(
              "r6",
              [{ ...agentEvent(1, "r6"), received_at: "2026-01-01T00:00:00.000Z" }],
              1,
            ),
          );
        return HttpResponse.json(
          eventsView(
            "r6",
            [{ ...agentEvent(2, "r6"), received_at: "2026-01-01T00:00:01.000Z" }],
            2,
          ),
        );
      }),
    );
    const { result } = renderHook(() => useRunEvents("r6", true), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.events).toHaveLength(2), {
      timeout: 4000,
    });
    // The second batch really did just arrive — client stamp, so the old server receipt
    // time can't hold the stall clock in the past while telemetry is visibly flowing.
    expect(result.current.lastEventAt).toBeGreaterThanOrEqual(testStart);
  });

  it("reorders a delayed older event into producer chronology across polling pages", async () => {
    let poll = 0;
    server.use(
      http.get("*/api/runs/r7/events", () => {
        poll += 1;
        if (poll === 1) {
          return HttpResponse.json(
            eventsView(
              "r7",
              [
                {
                  ...agentEvent(0, "r7"),
                  id: "response",
                  ts: new Date(2000).toISOString(),
                },
              ],
              0,
            ),
          );
        }
        return HttpResponse.json(
          eventsView(
            "r7",
            [
              {
                ...agentEvent(1, "r7"),
                id: "delayed-prompt",
                ts: new Date(1000).toISOString(),
              },
            ],
            1,
          ),
        );
      }),
    );
    const { result } = renderHook(() => useRunEvents("r7", true), {
      wrapper: createQueryWrapper(),
    });

    await waitFor(() => expect(result.current.events).toHaveLength(2), {
      timeout: 4000,
    });
    expect(result.current.events.map((event) => event.id)).toEqual([
      "delayed-prompt",
      "response",
    ]);
  });
});
