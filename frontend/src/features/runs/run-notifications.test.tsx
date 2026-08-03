import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, fireEvent, waitFor, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { useQueryClient } from "@tanstack/react-query";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture } from "@/test/fixtures";
import { useToastStore, TOAST_AUTO_DISMISS_MS, TOAST_EXIT_MS } from "@/stores/toasts";
import { ToastHost } from "@/components/ui/toast";
import { useRuns } from "./queries";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { RunNotificationsWatcher } from "./run-notifications";

/** Watcher + host + observable probe of the shared ["runs"] cache, plus a
 *  button that forces the next poll (instead of waiting out the 5s interval). */
function Harness() {
  const qc = useQueryClient();
  const runs = useRuns();
  return (
    <>
      <RunNotificationsWatcher />
      <ToastHost />
      <span data-testid="first-state">{runs.data?.[0]?.state ?? "none"}</span>
      <span data-testid="run-count">{runs.data?.length ?? 0}</span>
      <button onClick={() => qc.invalidateQueries({ queryKey: ["runs"] })}>
        refetch
      </button>
    </>
  );
}

describe("RunNotificationsWatcher", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
  });

  it("toasts once when a run transitions to terminal (timeout → warn + partial-result link)", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/runs", () => {
        calls += 1;
        return HttpResponse.json(
          calls === 1
            ? [runFixture({ run_id: "r1", state: "active" })]
            : [
                runFixture({
                  run_id: "r1",
                  name: "midnight recon",
                  state: "terminal",
                  terminal_trigger: "timeout",
                  completed_at: "2026-06-29T10:10:00Z",
                }),
              ],
        );
      }),
    );
    renderWithProviders(<Harness />);

    await waitFor(() =>
      expect(screen.getByTestId("first-state")).toHaveTextContent("active"),
    );
    // Baseline snapshot: no toast.
    expect(useToastStore.getState().toasts).toHaveLength(0);

    fireEvent.click(screen.getByText("refetch"));
    expect(
      await screen.findByText(/Run timed out — midnight recon/),
    ).toBeInTheDocument();
    expect(useToastStore.getState().toasts).toHaveLength(1);
    const link = screen.getByRole("link", { name: /view partial result/i });
    expect(link).toHaveAttribute("href", "/runs/result?id=r1");

    // Dismiss X clears the toast (after animation completes).
    vi.useFakeTimers();
    try {
      act(() => {
        fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
        vi.advanceTimersByTime(TOAST_EXIT_MS);
      });
      expect(screen.queryByText(/Run timed out/)).toBeNull();
      expect(useToastStore.getState().toasts).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("never toasts for the initial snapshot or runs that finished before mount", async () => {
    let calls = 0;
    const preexisting = runFixture({
      run_id: "r1",
      state: "terminal",
      terminal_trigger: "done",
      completed_at: "2026-06-29T10:10:00Z",
    });
    server.use(
      http.get("*/api/runs", () => {
        calls += 1;
        return HttpResponse.json(
          calls === 1
            ? [preexisting]
            : [
                preexisting,
                // Unseen run, but it completed long before this session — stale, not news.
                runFixture({
                  run_id: "r2",
                  state: "terminal",
                  terminal_trigger: "done",
                  completed_at: "2026-06-29T11:00:00Z",
                }),
              ],
        );
      }),
    );
    renderWithProviders(<Harness />);

    await waitFor(() =>
      expect(screen.getByTestId("first-state")).toHaveTextContent("terminal"),
    );
    expect(useToastStore.getState().toasts).toHaveLength(0);

    fireEvent.click(screen.getByText("refetch"));
    await waitFor(() =>
      expect(screen.getByTestId("run-count")).toHaveTextContent("2"),
    );
    expect(useToastStore.getState().toasts).toHaveLength(0);
    expect(screen.queryByText(/Run completed/)).toBeNull();
  });

  it("warns when a live run has been silent past the threshold — including at mount", async () => {
    // Unlike terminal toasts there is no baseline suppression: a run found already-stalled
    // when the app opens is exactly the situation the global notification exists for.
    const tenMinAgo = new Date(Date.now() - 10 * 60_000).toISOString();
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({
            run_id: "r9",
            name: "quiet run",
            state: "active",
            last_telemetry_at: tenMinAgo,
          }),
        ]),
      ),
    );
    renderWithProviders(<Harness />);

    expect(await screen.findByText(/Agent inactive — quiet run/)).toBeInTheDocument();
    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(useToastStore.getState().toasts[0]?.tone).toBe("warn");
    const link = screen.getByRole("link", { name: /inspect run/i });
    expect(link).toHaveAttribute("href", "/runs/live?id=r9");
  });

  it("toasts a stall episode once, and re-arms when telemetry resumes then re-stalls", async () => {
    const tenMinAgo = new Date(Date.now() - 10 * 60_000).toISOString();
    const fiveMinAgo = new Date(Date.now() - 5 * 60_000).toISOString();
    let calls = 0;
    server.use(
      http.get("*/api/runs", () => {
        calls += 1;
        return HttpResponse.json([
          runFixture({
            run_id: "r9",
            name: "quiet run",
            state: "active",
            // Polls 1+2 report the SAME silence; poll 3 shows telemetry resumed and went
            // quiet again (a newer mark that is still past the threshold).
            last_telemetry_at: calls <= 2 ? tenMinAgo : fiveMinAgo,
          }),
        ]);
      }),
    );
    renderWithProviders(<Harness />);

    await screen.findByText(/Agent inactive — quiet run/);
    fireEvent.click(screen.getByText("refetch"));
    await waitFor(() => expect(calls).toBeGreaterThanOrEqual(2));
    expect(useToastStore.getState().toasts).toHaveLength(1);

    fireEvent.click(screen.getByText("refetch"));
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(2));
  });

  it("never stall-warns a terminal run or one that has not emitted yet", async () => {
    const tenMinAgo = new Date(Date.now() - 10 * 60_000).toISOString();
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({
            run_id: "t1",
            state: "terminal",
            terminal_trigger: "done",
            completed_at: "2026-06-29T10:10:00Z",
            last_telemetry_at: tenMinAgo,
          }),
          // Never-connected: no telemetry at all — the live page's Awaiting-agent state owns
          // that situation; silence-since-launch is not a "stall".
          runFixture({ run_id: "c1", state: "created" }),
        ]),
      ),
    );
    renderWithProviders(<Harness />);

    await waitFor(() =>
      expect(screen.getByTestId("run-count")).toHaveTextContent("2"),
    );
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});

describe("toast store", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
  });

  it("auto-dismisses a toast after the timeout", () => {
    vi.useFakeTimers();
    try {
      const id = useToastStore.getState().push({ tone: "info", title: "hello" });
      expect(useToastStore.getState().toasts.map((t) => t.id)).toContain(id);
      vi.advanceTimersByTime(TOAST_AUTO_DISMISS_MS - 1);
      expect(useToastStore.getState().toasts).toHaveLength(1);
      vi.advanceTimersByTime(1);
      // Toast is marked leaving, but not yet removed
      expect(useToastStore.getState().toasts).toHaveLength(1);
      expect(useToastStore.getState().toasts[0]?.leaving).toBe(true);
      // After exit animation completes, it's removed
      vi.advanceTimersByTime(TOAST_EXIT_MS);
      expect(useToastStore.getState().toasts).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("manual dismiss removes the toast and cancels its timer", () => {
    vi.useFakeTimers();
    try {
      const id = useToastStore.getState().push({ tone: "ok", title: "bye" });
      useToastStore.getState().dismiss(id);
      // Toast is marked leaving, but not yet removed
      expect(useToastStore.getState().toasts).toHaveLength(1);
      expect(useToastStore.getState().toasts[0]?.leaving).toBe(true);
      // After exit animation completes, it's removed
      vi.advanceTimersByTime(TOAST_EXIT_MS);
      expect(useToastStore.getState().toasts).toHaveLength(0);
      // Advancing past the auto-dismiss TTL must not throw or resurrect anything.
      vi.advanceTimersByTime(TOAST_AUTO_DISMISS_MS + 1);
      expect(useToastStore.getState().toasts).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
