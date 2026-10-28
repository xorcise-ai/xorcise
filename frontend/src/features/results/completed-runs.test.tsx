import { describe, it, expect, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { agentFixture, scoreFixture, summaryFixture } from "@/test/fixtures";
import type { RunScore, ScoreSummary } from "@/lib/api/types";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { CompletedRuns } from "./completed-runs";
import { RunStrip, StripColumn } from "./run-strip";

function serve(scores: RunScore[], summaries: ScoreSummary[]) {
  server.use(
    http.get("*/api/agents", () =>
      HttpResponse.json([
        agentFixture({ id: "agent-1", name: "scout" }),
        agentFixture({ id: "agent-2", name: "raptor" }),
      ]),
    ),
    http.get("*/api/results/runs", () => HttpResponse.json(scores)),
    http.get("*/api/results/summary", () => HttpResponse.json(summaries)),
  );
}

// The strip's group carries the card's summary in words: one per card.
const strips = () =>
  screen
    .getAllByRole("group")
    .map((g) => g.getAttribute("aria-label") ?? "")
    .filter((l) => / runs?, /.test(l));

const KINDS: RunScore[] = [
  scoreFixture({ run_id: "full" }),
  scoreFixture({
    run_id: "part",
    judge_status: "partial",
    judge: 0.4,
    judge_upper: 0.9,
    overall: 0.7,
    overall_upper: 0.95,
  }),
  scoreFixture({
    run_id: "nojudge",
    bucket: "no_judge",
    judge_status: "unavailable",
    judge: 0,
    overall: 0.5,
  }),
  scoreFixture({ run_id: "stopped", bucket: "stopped", outcome: "partial", terminal_trigger: "operator" }),
  scoreFixture({ run_id: "timeout", timed_out: true, outcome: "partial", terminal_trigger: "timeout" }),
  scoreFixture({
    run_id: "env",
    bucket: "infra_failed",
    outcome: "infra_failed",
    deterministic: null,
    judge: null,
    judge_status: null,
    overall: null,
  }),
];

describe("CompletedRuns: one card per agent, around its run strip", () => {
  it("keeps the server's ranking and its names, never sorting on its own", async () => {
    // The server ranks (best median first, unscored last): every view shows its order, so this
    // deliberately out-of-median order must survive.
    serve(
      [],
      [
        summaryFixture({ agent_id: "agent-1", agent_name: "scout", median: 0.6 }),
        summaryFixture({ agent_id: "agent-2", agent_name: "raptor", median: 0.8 }),
        summaryFixture({ agent_id: "ghost", agent_name: "ghost", median: null, scored: 0 }),
      ],
    );
    renderWithProviders(<CompletedRuns />);
    await waitFor(() => expect(strips()).toHaveLength(3));
    expect(strips().map((l) => l.split(":")[0])).toEqual(["scout", "raptor", "ghost"]);
  });

  it("says the median once, what it covers, and links the name to the profile", async () => {
    serve(
      [scoreFixture({ run_id: "s1" })],
      [summaryFixture({ agent_id: "agent-1", runs: 4, scored: 3, median: 0.75 })],
    );
    renderWithProviders(<CompletedRuns />);
    const name = await screen.findByRole("link", { name: "scout: view profile" });
    expect(name).toHaveAttribute("href", "/agents/detail?name=scout");
    expect(screen.getAllByText("75%")).toHaveLength(1);
    expect(screen.getAllByText("median")).toHaveLength(1);
    // " · 1 left out" is its own unbreakable span, so match the line by its whole text.
    expect(
      screen.getByText((_, el) => el?.textContent === "3 of 4 runs in the median · 1 left out" && el.tagName === "SPAN" && !!el.querySelector("span")),
    ).toBeInTheDocument();
  });

  it("tells each kind of run apart by the column itself, with no marks beside it", async () => {
    serve(KINDS, [summaryFixture({ agent_id: "agent-1" })]);
    renderWithProviders(<CompletedRuns />);
    await waitFor(() => expect(screen.getAllByTestId("strip-column")).toHaveLength(6));
    const col = (kind: string) =>
      screen.getAllByTestId("strip-column").find((c) => c.dataset.kind === kind)!;
    expect(col("scored").querySelector("[data-part=cap], [data-part=unmeasured]")).toBeNull();
    expect(col("partial_judge").querySelector('[data-part="judge-range"]')).not.toBeNull();
    // The judge half that was never measured is an outline, not a zero.
    expect(col("no_judge").querySelector('[data-part="unmeasured"]')).not.toBeNull();
    // Stopped: grey. Timed out: a red cap, in full colour, since it counts.
    expect(col("stopped").querySelector('[data-part="bar"]')!.className).toContain("grayscale");
    expect(col("timed_out").querySelector('[data-part="cap"]')!.className).toContain("bg-err");
    expect(col("timed_out").querySelector('[data-part="bar"]')!.className).not.toContain("grayscale");
    // An environment failure is an empty striped slot: never a score, never red.
    const env = col("infra_failed").querySelector('[data-part="infra"]')!;
    expect(env.className).not.toContain("err");
    // Each column opens its run, and says what it is.
    expect(col("timed_out")).toHaveAttribute("href", "/runs/result?id=timeout");
    expect(col("timed_out")).toHaveAccessibleName(/timed out · in the median/);
    expect(col("stopped")).toHaveAccessibleName(/stopped · left out/);
  });

  it("keeps the red cap on a run that timed out having scored nothing", () => {
    const { container } = renderWithProviders(
      <StripColumn
        run={scoreFixture({
          run_id: "t0",
          timed_out: true,
          outcome: "partial",
          terminal_trigger: "timeout",
          overall: 0,
          deterministic: 0,
          judge: 0,
        })}
      />,
    );
    const bar = container.querySelector<HTMLElement>('[data-part="bar"]')!;
    expect(bar.style.height).toBe("0%");
    // A bar at 0% clips everything in it: its floor is the cap's height, so the cap still shows.
    expect(bar.className).toContain("min-h-1");
    expect(bar.querySelector('[data-part="cap"]')!.className).toContain("h-1");
  });

  it("opens the inspector on a column and lights up its kind in the key", async () => {
    serve(KINDS, [summaryFixture({ agent_id: "agent-1" })]);
    renderWithProviders(<CompletedRuns />);
    await waitFor(() => expect(screen.getAllByTestId("strip-column")).toHaveLength(6));
    const timeout = screen.getAllByTestId("strip-column").find((c) => c.dataset.kind === "timed_out")!;
    fireEvent.mouseEnter(timeout);
    const inspector = screen.getByTestId("run-inspector");
    expect(inspector).toHaveTextContent("Timed out · in the median");
    expect(inspector).toHaveTextContent("Not finishing counts against the agent");
    const keyItem = within(screen.getByRole("list", { name: "In the median" }))
      .getAllByRole("listitem")
      .find((li) => li.dataset.kind === "timed_out")!;
    expect(keyItem).toHaveAttribute("data-active", "true");
    fireEvent.mouseLeave(timeout);
    expect(screen.queryByTestId("run-inspector")).toBeNull();
    expect(keyItem).not.toHaveAttribute("data-active");
  });

  it("is one tab stop on the newest run, with the arrow keys moving between runs", async () => {
    serve(KINDS, [summaryFixture({ agent_id: "agent-1" })]);
    renderWithProviders(<CompletedRuns />);
    await waitFor(() => expect(screen.getAllByTestId("strip-column")).toHaveLength(6));
    const cols = screen.getAllByTestId("strip-column");
    expect(cols.map((c) => c.tabIndex)).toEqual([-1, -1, -1, -1, -1, 0]);
    act(() => cols[5].focus());
    expect(screen.getByTestId("run-inspector")).toHaveTextContent("Environment failure · left out");
    fireEvent.keyDown(cols[5], { key: "ArrowLeft" });
    expect(document.activeElement).toBe(cols[4]);
    fireEvent.keyDown(cols[4], { key: "Home" });
    expect(document.activeElement).toBe(cols[0]);
  });

  it("keeps the tab stop and the inspector on their run when a new run shifts a full strip", () => {
    // jsdom cannot measure, so the strip fits its fallback of 24: full at 24 runs.
    const runs = Array.from({ length: 24 }, (_, i) => scoreFixture({ run_id: `r${i}` }));
    const { rerender } = renderWithProviders(<RunStrip runs={runs} median={0.5} summary="s" />);
    const before = screen.getAllByTestId("strip-column");
    act(() => before[10].focus());
    expect(before[10]).toHaveAttribute("href", "/runs/result?id=r10");

    rerender(
      <RunStrip runs={[...runs, scoreFixture({ run_id: "r24" })]} median={0.5} summary="s" />,
    );
    const after = screen.getAllByTestId("strip-column");
    const held = after.find((c) => c.tabIndex === 0)!;
    expect(held).toHaveAttribute("href", "/runs/result?id=r10");
    expect(after.indexOf(held)).toBe(9);
    expect(after[9].className).toContain("ring-1");
  });

  it("shows the latest runs that fit, and says how many there are in all", async () => {
    const runs = Array.from({ length: 30 }, (_, i) => scoreFixture({ run_id: `r${i}` }));
    serve(runs, [summaryFixture({ agent_id: "agent-1", runs: 30, scored: 30 })]);
    renderWithProviders(<CompletedRuns />);
    // jsdom cannot measure, so the strip draws its fallback of 24.
    expect(await screen.findByText("latest 24 of 30")).toBeInTheDocument();
    expect(screen.getAllByTestId("strip-column")).toHaveLength(24);
  });

  it("draws the median line at the median, and names what the median leaves out", async () => {
    serve(
      [scoreFixture()],
      [summaryFixture({ agent_id: "agent-1", median: 0.62, no_judge: 2, stopped: 1 })],
    );
    renderWithProviders(<CompletedRuns />);
    const line = await screen.findByTestId("strip-median");
    expect(line.style.bottom).toBe("62%");
    expect(strips()[0]).toContain("2 no judge · 1 stopped, not in the median");
  });

  it("shows a what/why/next empty state with Start run when nothing has finished", async () => {
    serve([], []);
    renderWithProviders(<CompletedRuns />);
    await waitFor(() => expect(screen.getByText(/No results yet/i)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /Start run/i })).toHaveAttribute("href", "/runs/new");
  });
});
