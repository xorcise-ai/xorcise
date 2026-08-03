import { describe, it, expect } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { createQueryWrapper } from "@/test/render";
import { agentFixture } from "@/test/fixtures";
import {
  useAgents,
  useRegisterAgent,
  useUpdateAgent,
  useAgentHistory,
} from "./queries";

describe("agents queries", () => {
  it("useAgents lists agents from the server", async () => {
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([agentFixture({ name: "scout" })]),
      ),
    );
    const { result } = renderHook(() => useAgents(), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.data?.[0]?.name).toBe("scout"));
  });

  it("useRegisterAgent POSTs a declaration and refetches the list", async () => {
    let registered = false;
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json(
          registered ? [agentFixture({ name: "scout" })] : [],
        ),
      ),
      http.post("*/api/agents", () => {
        registered = true;
        return HttpResponse.json(agentFixture({ name: "scout" }), {
          status: 201,
        });
      }),
    );
    const { result } = renderHook(
      () => ({ list: useAgents(), register: useRegisterAgent() }),
      { wrapper: createQueryWrapper() },
    );
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
    expect(result.current.list.data).toHaveLength(0);

    await act(async () => {
      await result.current.register.mutateAsync({
        name: "scout",
        endpoint: null,
        otel: null,
      });
    });
    await waitFor(() =>
      expect(result.current.list.data?.[0]?.name).toBe("scout"),
    );
  });

  it("useUpdateAgent PUTs to /agents/{name} and refetches the list", async () => {
    let putName: string | null = null;
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ name: "scout", model: putName ? "m2" : "m1" }),
        ]),
      ),
      http.put("*/api/agents/scout", async ({ request }) => {
        putName = "scout";
        const body = (await request.json()) as { model?: string };
        return HttpResponse.json(agentFixture({ name: "scout", model: body.model }));
      }),
    );
    const { result } = renderHook(
      () => ({ list: useAgents(), update: useUpdateAgent() }),
      { wrapper: createQueryWrapper() },
    );
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));

    await act(async () => {
      await result.current.update.mutateAsync({
        name: "scout",
        decl: { name: "scout", endpoint: null, otel: null, model: "m2" },
      });
    });
    expect(putName).toBe("scout");
    await waitFor(() =>
      expect(result.current.list.data?.[0]?.model).toBe("m2"),
    );
  });

  it("agent mutations invalidate the list EXACTLY, never the per-name history", async () => {
    // The ["agents"] prefix also covers ["agents", name, "history"]. A prefix
    // invalidation would refetch a name the PUT has just retired — a guaranteed 404.
    const historyCalls: string[] = [];
    let listCalls = 0;
    server.use(
      http.get("*/api/agents", () => {
        listCalls += 1;
        return HttpResponse.json([agentFixture({ name: "scout" })]);
      }),
      http.get("*/api/agents/:name/history", ({ params }) => {
        historyCalls.push(String(params.name));
        return HttpResponse.json([]);
      }),
      http.put("*/api/agents/scout", () =>
        HttpResponse.json(agentFixture({ name: "scout2" })),
      ),
    );
    const { result } = renderHook(
      () => ({
        list: useAgents(),
        history: useAgentHistory("scout"),
        update: useUpdateAgent(),
      }),
      { wrapper: createQueryWrapper() },
    );
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
    await waitFor(() => expect(historyCalls).toEqual(["scout"]));
    const listBefore = listCalls;

    await act(async () => {
      await result.current.update.mutateAsync({
        name: "scout",
        decl: { name: "scout2", endpoint: null, otel: null },
      });
    });

    // The list refetches (the rename changed it) …
    expect(listCalls).toBeGreaterThan(listBefore);
    // … but the still-mounted old-name history query is left alone.
    expect(historyCalls).toEqual(["scout"]);
  });

  it("useAgentHistory fetches per-agent run history", async () => {
    server.use(
      http.get("*/api/agents/scout/history", () =>
        HttpResponse.json([
          {
            agent_id: "agent-1",
            run_id: "run-1",
            overall: 0.8,
            deterministic: 0.9,
            judge: 0.7,
            trace_ref: null,
            created_at: "2026-06-29T10:00:00Z",
          },
        ]),
      ),
    );
    const { result } = renderHook(() => useAgentHistory("scout"), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.data?.[0]?.run_id).toBe("run-1"));
  });
});
