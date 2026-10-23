import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { scoreFixture, summaryFixture } from "@/test/fixtures";
import type { RunScore } from "@/lib/api/types";

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

import { RunLedger, ledgerRows } from "./run-ledger";

const name = (slug: string) => (slug === "sqli-login" ? "SQLi Login" : slug);
const v = (mission: string, version: string, current: boolean, last: string) =>
  summaryFixture({ mission, mission_version: version, current_version: current, last_run: last });

describe("ledgerRows", () => {
  it("keeps a mission's versions together, current first, most recently run mission first", () => {
    const rows = ledgerRows(
      [
        v("sqli-login", "1.0.0", false, "2026-06-01T00:00:00Z"),
        v("xss", "1.0.0", true, "2026-06-10T00:00:00Z"),
        v("sqli-login", "2.0.0", true, "2026-06-20T00:00:00Z"),
      ],
      [
        scoreFixture({ run_id: "a", mission: "sqli-login", mission_version: "2.0.0" }),
        scoreFixture({ run_id: "b", mission: "sqli-login", mission_version: "1.0.0" }),
      ],
      name,
    );
    expect(rows.map((r) => `${r.summary.mission}@${r.summary.mission_version}`)).toEqual([
      "sqli-login@2.0.0",
      "sqli-login@1.0.0",
      "xss@1.0.0",
    ]);
    expect(rows[0].name).toBe("SQLi Login");
    expect(rows[0].runs.map((r) => r.run_id)).toEqual(["a"]);
    expect(rows[2].runs).toEqual([]);
  });
});

function ledger(runsA: RunScore[], runsB: RunScore[] = []) {
  const rows = [
    {
      summary: v("sqli-login", "2.0.0", true, "2026-06-20T00:00:00Z"),
      name: "SQLi Login",
      runs: runsA,
    },
    {
      summary: v("sqli-login", "1.0.0", false, "2026-06-01T00:00:00Z"),
      name: "SQLi Login",
      runs: runsB,
    },
  ];
  render(<RunLedger rows={rows} />);
  return screen.getAllByTestId("ledger-row");
}

describe("RunLedger", () => {
  it("gives an older mission version its own row, marked as older", () => {
    const [current, older] = ledger([scoreFixture({ run_id: "a" })], [scoreFixture({ run_id: "b" })]);
    expect(within(current).queryByText("older")).toBeNull();
    expect(within(older).getByText("older")).toBeInTheDocument();
    expect(older).toHaveAccessibleName("SQLi Login v1.0.0, older version");
  });

  it("opens each run's result from its cell, which says what the run scored", () => {
    ledger([scoreFixture({ run_id: "run-7" })]);
    const cell = screen.getByTestId("ledger-cell");
    expect(cell).toHaveAttribute("href", "/runs/result?id=run-7");
    expect(cell).toHaveAccessibleName(/deterministic 100% · judge 80% · overall 90%/);
  });

  it("draws each kind of run by what was measured", () => {
    ledger([
      scoreFixture({ run_id: "full" }),
      scoreFixture({ run_id: "nj", bucket: "no_judge", judge_status: "unavailable", judge: 0 }),
      scoreFixture({
        run_id: "part",
        judge_status: "partial",
        judge: 0.3,
        judge_upper: 0.8,
      }),
      scoreFixture({
        run_id: "env",
        bucket: "infra_failed",
        outcome: "infra_failed",
        deterministic: null,
        judge: null,
        overall: null,
        judge_status: null,
      }),
      scoreFixture({ run_id: "stop", bucket: "stopped", outcome: "partial", terminal_trigger: "operator" }),
      scoreFixture({ run_id: "late", timed_out: true, outcome: "partial", terminal_trigger: "timeout" }),
    ]);
    const [full, nj, part, env, stopped, timedOut] = screen.getAllByTestId("ledger-cell");
    expect(full.querySelector('[data-part="judge"]')).not.toBeNull();
    expect(nj.querySelector('[data-part="unmeasured"]')).not.toBeNull();
    expect(part.querySelector('[data-part="judge-range"]')).not.toBeNull();
    // An environment failure is an empty striped cell: never a score, never red.
    expect(env.querySelector('[data-part="infra"]')).not.toBeNull();
    expect(env.className).not.toContain("err");
    expect(env).toHaveAccessibleName(/environment failure/);
    // Stopped: grey, left out. Timed out: a red cap, in full colour, since it counts.
    expect(stopped.className).toContain("grayscale");
    expect(timedOut.querySelector('[data-part="cap"]')!.className).toContain("bg-err");
    expect(timedOut.className).not.toContain("grayscale");
    expect(timedOut).toHaveAccessibleName(/timed out · in the median/);
  });

  it("is one tab stop, on the newest run, and the arrow keys move between runs", () => {
    ledger(
      [scoreFixture({ run_id: "a1" }), scoreFixture({ run_id: "a2" })],
      [scoreFixture({ run_id: "b1" })],
    );
    const cells = screen.getAllByTestId("ledger-cell");
    expect(cells.map((c) => c.tabIndex)).toEqual([-1, 0, -1]);
    cells[1].focus();
    fireEvent.keyDown(cells[1], { key: "ArrowLeft" });
    expect(document.activeElement).toBe(cells[0]);
    fireEvent.keyDown(cells[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(cells[2]);
    fireEvent.keyDown(cells[2], { key: "ArrowUp" });
    expect(document.activeElement).toBe(cells[0]);
    fireEvent.keyDown(cells[0], { key: "End" });
    expect(document.activeElement).toBe(cells[1]);
  });

  it("draws a run whose grading failed as an empty slot, and a stopped one never judged as unmeasured", () => {
    ledger([
      scoreFixture({
        run_id: "gf",
        bucket: "grading_failed",
        judge_status: "unavailable",
        deterministic: 0,
        judge: 0,
        overall: 0,
      }),
      scoreFixture({
        run_id: "sn",
        bucket: "stopped",
        terminal_trigger: "operator",
        judge_status: "unavailable",
        judge: 0,
      }),
    ]);
    const [failed, stopped] = screen.getAllByTestId("ledger-cell");
    expect(failed.dataset.kind).toBe("grading_failed");
    expect(failed.className).toContain("border-dashed");
    expect(failed.className).not.toContain("err");
    expect(failed.querySelector('[data-part="judge"]')).toBeNull();
    expect(failed).toHaveAccessibleName(/grading failed/);
    // Its judge never ran: the judge half is unmeasured, not a zero.
    expect(stopped.querySelector('[data-part="unmeasured"]')).not.toBeNull();
    expect(stopped).toHaveAccessibleName(/no judge · overall 90% with the judge half unmeasured/);
  });

  it("keeps the tab stop on the same run when a refetch adds a row above it", () => {
    const row = (version: string, runs: RunScore[]) => ({
      summary: v("sqli-login", version, version === "3.0.0", "2026-06-20T00:00:00Z"),
      name: "SQLi Login",
      runs,
    });
    const b = row("1.0.0", [scoreFixture({ run_id: "b1" }), scoreFixture({ run_id: "b2" })]);
    const { rerender } = render(
      <RunLedger rows={[row("2.0.0", [scoreFixture({ run_id: "a1" })]), b]} />,
    );
    const b1 = screen.getAllByTestId("ledger-cell").find((c) => c.getAttribute("href")?.endsWith("b1"))!;
    b1.focus();
    rerender(
      <RunLedger
        rows={[row("3.0.0", [scoreFixture({ run_id: "n1" })]), row("2.0.0", [scoreFixture({ run_id: "a1" })]), b]}
      />,
    );
    const stops = screen.getAllByTestId("ledger-cell").filter((c) => c.tabIndex === 0);
    expect(stops.map((c) => c.getAttribute("href"))).toEqual(["/runs/result?id=b1"]);
  });

  it("shows the latest runs that fit and says how many earlier ones there are", () => {
    const runs = Array.from({ length: 40 }, (_, i) => scoreFixture({ run_id: `r${i}` }));
    ledger(runs);
    // jsdom cannot measure, so a row draws its fallback of 32.
    expect(screen.getAllByTestId("ledger-cell")).toHaveLength(32);
    expect(screen.getByText("+8 earlier ·")).toBeInTheDocument();
    expect(screen.getAllByTestId("ledger-cell").at(-1)).toHaveAttribute("href", "/runs/result?id=r39");
  });

  it("opens the inspector on a cell and reports its kind", () => {
    const kinds: (string | null)[] = [];
    render(
      <RunLedger
        rows={[
          {
            summary: v("sqli-login", "2.0.0", true, "2026-06-20T00:00:00Z"),
            name: "SQLi Login",
            runs: [scoreFixture({ run_id: "a" }), scoreFixture({ run_id: "b", bucket: "stopped", terminal_trigger: "operator" })],
          },
        ]}
        onInspect={(k) => kinds.push(k)}
      />,
    );
    const [, stopped] = screen.getAllByTestId("ledger-cell");
    fireEvent.mouseEnter(stopped);
    expect(screen.getByTestId("run-inspector")).toHaveTextContent("Stopped · left out");
    fireEvent.mouseLeave(stopped);
    expect(screen.queryByTestId("run-inspector")).toBeNull();
    expect(kinds).toEqual(["stopped", null]);
  });
});
