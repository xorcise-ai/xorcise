import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { AgentEvent, AgentEventKind } from "@/lib/api/types";
import { EventCard, permissionGateExplanation, prettyValue, stripAnsi } from "./event-card";

// ESC (0x1B) built at runtime — no literal control byte in the test source.
const ESC = String.fromCharCode(27);

function agentEvent(overrides: Partial<AgentEvent> = {}): AgentEvent {
  return {
    run_id: "r1",
    id: "evt-1",
    ts: "2026-07-03T10:00:00Z",
    source_agent: "generic",
    kind: "message",
    role: "agent",
    title: "Agent message",
    body: "Here is what I found.",
    data: {},
    severity: "info",
    raw_ref: { run_id: "r1", raw_seq: 1, span_id: "s1", signal: "trace" },
    ...overrides,
  };
}

describe("EventCard", () => {
  it("renders a message event's body as prose", () => {
    render(<EventCard event={agentEvent({ kind: "message" })} onViewRaw={() => {}} />);
    expect(screen.getByText("Here is what I found.")).toBeInTheDocument();
  });

  it("renders a terminal_command event's body in a monospace/<pre> block", () => {
    render(
      <EventCard
        event={agentEvent({ kind: "terminal_command", body: "whoami", title: "exec_shell" })}
        onViewRaw={() => {}}
      />,
    );
    // The command text is syntax-highlighted, so "whoami" sits in a coloured <span> inside the
    // <pre> — assert the <pre> ancestor rather than the exact node.
    const el = screen.getByText("whoami");
    expect(el.closest("pre")).not.toBeNull();
  });

  it.each<AgentEventKind>(["flag", "finding"])(
    "renders an agent-claimed label for a %s event",
    (kind) => {
      render(<EventCard event={agentEvent({ kind, title: "possible SQLi" })} onViewRaw={() => {}} />);
      expect(screen.getByText(/agent-claimed/i)).toBeInTheDocument();
    },
  );

  it("uses error styling for an error event", () => {
    render(
      <EventCard
        event={agentEvent({ kind: "error", body: "boom", severity: "error" })}
        onViewRaw={() => {}}
      />,
    );
    expect(screen.getByText("boom")).toHaveClass("text-err");
  });

  it("renders an unknown event without throwing (title + raw affordance)", () => {
    expect(() =>
      render(
        <EventCard
          event={agentEvent({ kind: "unknown", title: "mystery span", body: "" })}
          onViewRaw={() => {}}
        />,
      ),
    ).not.toThrow();
    expect(screen.getByText("mystery span")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /view raw/i })).toBeInTheDocument();
  });

  it("calls onViewRaw(event) from the view-raw control", () => {
    const onViewRaw = vi.fn();
    const event = agentEvent({ id: "evt-42" });
    render(<EventCard event={event} onViewRaw={onViewRaw} />);
    fireEvent.click(screen.getByRole("button", { name: /view raw/i }));
    expect(onViewRaw).toHaveBeenCalledWith(event);
  });

  it("pretty-prints a JSON data value in a contained <pre> block", () => {
    render(
      <EventCard
        event={agentEvent({
          kind: "tool_call",
          body: "",
          data: { input: '{"command":"create","path":"/workspace/ca.pem"}' },
        })}
        onViewRaw={() => {}}
      />,
    );
    const pre = document.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain('"command": "create"'); // indented (space after colon)
    expect(pre!.textContent).toContain("\n"); // multi-line => pretty-printed
  });

  it("wraps long unbroken tokens instead of overflowing (break-words on prose)", () => {
    const longToken = "A".repeat(400);
    render(
      <EventCard event={agentEvent({ kind: "message", body: longToken })} onViewRaw={() => {}} />,
    );
    expect(screen.getByText(longToken)).toHaveClass("break-words");
  });

  it("strips ANSI escape sequences from terminal output", () => {
    render(
      <EventCard
        event={agentEvent({
          kind: "terminal_output",
          body: `ready${ESC}[?2004ldone${ESC}[0m`,
        })}
        onViewRaw={() => {}}
      />,
    );
    const pre = document.querySelector("pre");
    expect(pre?.textContent).toBe("readydone");
  });

  it("shows the display-layer label, keeping the raw adapter title as the hover tooltip", () => {
    render(
      <EventCard
        event={agentEvent({ kind: "message", role: "agent", title: "assistant message" })}
        onViewRaw={() => {}}
      />,
    );
    const label = screen.getByText("Agent CoT");
    expect(label).toBeInTheDocument();
    expect(label.getAttribute("title")).toBe("assistant message");
    expect(screen.queryByText("assistant message")).not.toBeInTheDocument();
  });

  it("labels a user message as User Prompt with the prompt badge", () => {
    render(
      <EventCard
        event={agentEvent({ kind: "message", role: "user", title: "user message" })}
        onViewRaw={() => {}}
      />,
    );
    expect(screen.getByText("User Prompt")).toBeInTheDocument();
    expect(screen.getByText("prompt")).toBeInTheDocument();
  });

  it("keeps the adapter's '{tool} {path}' as a subtitle on file ops", () => {
    render(
      <EventCard
        event={agentEvent({ kind: "file_edit", title: "str_replace /workspace/ca.pem", body: "" })}
        onViewRaw={() => {}}
      />,
    );
    expect(screen.getByText("Agent File Edit")).toBeInTheDocument();
    expect(screen.getByText("str_replace /workspace/ca.pem")).toBeInTheDocument();
  });

  it("renders the event time on every card, with the duration when present", () => {
    render(
      <EventCard
        event={agentEvent({ ts: "2026-07-03T10:00:00Z", duration_ms: 4200 })}
        onViewRaw={() => {}}
      />,
    );
    const time = screen.getByTestId("event-time");
    expect(time.textContent).toMatch(/\d{2}:\d{2}:\d{2}/);
    expect(time.textContent).toContain("4.2s");
  });

  it("stamps data-event-id for scroll-to-event targeting", () => {
    const { container } = render(
      <EventCard event={agentEvent({ id: "evt-77" })} onViewRaw={() => {}} />,
    );
    expect(container.querySelector('[data-event-id="evt-77"]')).not.toBeNull();
  });

  it("renders the same structural wrapper regardless of source_agent (guard: kind-only dispatch)", () => {
    const { container: openhands } = render(
      <EventCard
        event={agentEvent({ kind: "tool_call", source_agent: "openhands" })}
        onViewRaw={() => {}}
      />,
    );
    const openhandsCard = openhands.querySelector("[data-kind]");
    const { container: generic } = render(
      <EventCard
        event={agentEvent({ kind: "tool_call", source_agent: "generic" })}
        onViewRaw={() => {}}
      />,
    );
    const genericCard = generic.querySelector("[data-kind]");
    expect(openhandsCard).not.toBeNull();
    expect(openhandsCard?.getAttribute("data-kind")).toBe("tool_call");
    expect(openhandsCard?.className).toBe(genericCard?.className);
    expect(openhandsCard?.getAttribute("data-kind")).toBe(
      genericCard?.getAttribute("data-kind"),
    );
  });

  it("renders byte-identical markup for body+data content regardless of source_agent (guard: no body-level source_agent branch)", () => {
    const withBodyAndData = agentEvent({
      kind: "tool_call",
      body: "reading /etc/passwd",
      data: { path: "/etc/passwd", mode: "r" },
    });
    const { container: openhands } = render(
      <EventCard event={{ ...withBodyAndData, source_agent: "openhands" }} onViewRaw={() => {}} />,
    );
    const { container: generic } = render(
      <EventCard event={{ ...withBodyAndData, source_agent: "generic" }} onViewRaw={() => {}} />,
    );
    expect(openhands.innerHTML).toBe(generic.innerHTML);
  });
});

describe("prettyValue", () => {
  it("pretty-prints a JSON object string", () => {
    expect(prettyValue('{"a":1}')).toBe('{\n  "a": 1\n}');
  });
  it("returns a non-JSON string unchanged", () => {
    expect(prettyValue("-----BEGIN CERTIFICATE-----")).toBe("-----BEGIN CERTIFICATE-----");
  });
  it("returns invalid JSON-ish text unchanged", () => {
    expect(prettyValue("{not valid json")).toBe("{not valid json");
  });
});

describe("stripAnsi", () => {
  it("removes CSI sequences including bracketed-paste and SGR colours", () => {
    expect(stripAnsi(`a${ESC}[?2004lb${ESC}[0mc`)).toBe("abc");
  });
  it("leaves plain text untouched", () => {
    expect(stripAnsi("plain text — no escapes")).toBe("plain text — no escapes");
  });
});

describe("permissionGateExplanation", () => {
  it("explains a rejected gate", () => {
    const explanation = permissionGateExplanation(
      agentEvent({ kind: "status", title: "permission gate", data: { decision: "reject" } }),
    );
    expect(explanation).toContain("Blocked by the permission gate");
    expect(explanation).toContain("did NOT run");
  });
  it("explains an approved gate", () => {
    const explanation = permissionGateExplanation(agentEvent({ kind: "status", data: { decision: "approve" } }));
    expect(explanation).toContain("Approved by the permission gate");
  });
  it("returns null for a non-gate event (no decision)", () => {
    expect(permissionGateExplanation(agentEvent({ kind: "message" }))).toBeNull();
  });
  it("renders the tooltip on the card for a permission gate", () => {
    render(
      <EventCard
        event={agentEvent({ kind: "status", title: "permission gate", data: { decision: "reject" } })}
        onViewRaw={() => {}}
      />,
    );
    const card = document.querySelector('[data-kind="status"]');
    expect(card?.getAttribute("title")).toContain("Blocked by the permission gate");
  });
});
