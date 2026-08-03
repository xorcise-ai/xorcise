import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import type { AgentEvent } from "@/lib/api/types";
import { RawDrillDownModal } from "./raw-drilldown-modal";

function agentEvent(overrides: Partial<AgentEvent> = {}): AgentEvent {
  return {
    run_id: "r1",
    id: "evt-1",
    ts: "2026-07-03T10:00:00Z",
    source_agent: "generic",
    kind: "terminal_command",
    role: "agent",
    title: "exec_shell",
    body: "whoami",
    data: {},
    severity: "info",
    raw_ref: { run_id: "r1", raw_seq: 1, span_id: "s1", signal: "trace" },
    ...overrides,
  };
}

describe("RawDrillDownModal", () => {
  it("fetches the raw span(s) on open and shows them as JSON", async () => {
    server.use(
      http.get("*/api/runs/r1/events/evt-1/raw", () =>
        HttpResponse.json({
          event_id: "evt-1",
          raw_ref: {
            run_id: "r1",
            raw_seq: 1,
            span_id: "s1",
            signal: "trace",
          },
          spans: [{ spanId: "s1", name: "exec_shell" }],
          logs: [],
        }),
      ),
    );
    render(
      <RawDrillDownModal
        runId="r1"
        event={agentEvent()}
        open={true}
        onClose={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByText(/"spanId": "s1"/)).toBeInTheDocument());
    expect(screen.getByText(/"name": "exec_shell"/)).toBeInTheDocument();
  });

  it("shows raw log records for a log-derived event", async () => {
    server.use(
      http.get("*/api/runs/r1/events/log-0/raw", () =>
        HttpResponse.json({
          event_id: "log-0",
          raw_ref: {
            run_id: "r1",
            raw_seq: 0,
            span_id: "",
            signal: "log",
          },
          spans: [],
          logs: [{ body: { stringValue: "claude_code.assistant_response" } }],
        }),
      ),
    );
    render(
      <RawDrillDownModal
        runId="r1"
        event={agentEvent({
          id: "log-0",
          raw_ref: {
            run_id: "r1",
            raw_seq: 0,
            span_id: "",
            signal: "log",
          },
        })}
        open={true}
        onClose={() => {}}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/claude_code.assistant_response/)).toBeInTheDocument(),
    );
  });

  it("closing calls onClose", async () => {
    server.use(
      http.get("*/api/runs/r1/events/evt-1/raw", () =>
        HttpResponse.json({ event_id: "evt-1", raw_ref: null, spans: [], logs: [] }),
      ),
    );
    const onClose = vi.fn();
    render(
      <RawDrillDownModal runId="r1" event={agentEvent()} open={true} onClose={onClose} />,
    );
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("URL-encodes an event id containing '/' while preserving segment structure", async () => {
    server.use(
      http.get("*/api/runs/r1/events/base64/with-slash/raw", () =>
        HttpResponse.json({
          event_id: "base64/with-slash",
          raw_ref: null,
          spans: [],
          logs: [],
        }),
      ),
    );
    render(
      <RawDrillDownModal
        runId="r1"
        event={agentEvent({ id: "base64/with-slash" })}
        open={true}
        onClose={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
  });

  it("renders nothing when closed", () => {
    render(
      <RawDrillDownModal runId="r1" event={agentEvent()} open={false} onClose={() => {}} />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
