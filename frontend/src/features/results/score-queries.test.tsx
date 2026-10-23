import { describe, it, expect } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { runFixture, summaryFixture } from "@/test/fixtures";
import type { RunEntry } from "@/lib/api/types";
import { useScoreSummary } from "./queries";

// The performance views refetch when a run finishes or is deleted, from the runs list the app
// already polls: no mutation has to remember to invalidate them.
describe("the performance queries follow the runs list", () => {
  it("refetch when a run finishes or is deleted, and not before the runs list answers", async () => {
    let runs: RunEntry[] = [runFixture({ run_id: "r1", state: "terminal", terminal_trigger: "done" })];
    let summaryCalls = 0;
    server.use(
      http.get("*/api/runs", () => HttpResponse.json(runs)),
      http.get("*/api/results/summary", () => {
        summaryCalls += 1;
        return HttpResponse.json([summaryFixture({ runs: summaryCalls })]);
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useScoreSummary("all"), { wrapper });
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.data?.[0].runs).toBe(1));
    expect(summaryCalls).toBe(1);

    // A second run finishes.
    runs = [...runs, runFixture({ run_id: "r2", state: "terminal", terminal_trigger: "done" })];
    await act(() => client.invalidateQueries({ queryKey: ["runs"], exact: true }));
    await waitFor(() => expect(result.current.data?.[0].runs).toBe(2));

    // A run still going changes nothing.
    runs = [...runs, runFixture({ run_id: "r3", state: "active", terminal_trigger: null })];
    await act(() => client.invalidateQueries({ queryKey: ["runs"], exact: true }));
    expect(summaryCalls).toBe(2);

    // A finished run is deleted.
    runs = runs.filter((r) => r.run_id !== "r1");
    await act(() => client.invalidateQueries({ queryKey: ["runs"], exact: true }));
    await waitFor(() => expect(result.current.data?.[0].runs).toBe(3));
  });
});
