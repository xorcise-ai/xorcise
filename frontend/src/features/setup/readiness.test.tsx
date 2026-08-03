import { describe, it, expect } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { createQueryWrapper } from "@/test/render";
import { agentFixture, missionFixture } from "@/test/fixtures";
import { useReadiness } from "./readiness";

function systemReady() {
  return HttpResponse.json({
    role: "all",
    planes: [{ name: "docker", ok: true, detail: "ok", location: "local daemon" }],
    db_schema: "head",
    catalog: { state: "connected", message: null, last_sync: null },
    remotes: [],
    home: "/h",
    db_url: "sqlite:///x",
    topology: "local",
  });
}

describe("useReadiness", () => {
  it("is ready with docker up, an agent, and a mission — start goes to /runs/new", async () => {
    server.use(
      http.get("*/api/system", () => systemReady()),
      http.get("*/api/agents", () => HttpResponse.json([agentFixture()])),
      http.get("*/api/missions", () =>
        HttpResponse.json([missionFixture({ installed: true })]),
      ),
    );
    const { result } = renderHook(() => useReadiness(), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.ready).toBe(true);
    expect(result.current.startHref).toBe("/runs/new");
  });

  it("is not ready without an agent — start routes to /agents", async () => {
    server.use(
      http.get("*/api/system", () => systemReady()),
      http.get("*/api/agents", () => HttpResponse.json([])),
      http.get("*/api/missions", () =>
        HttpResponse.json([missionFixture({ installed: true })]),
      ),
    );
    const { result } = renderHook(() => useReadiness(), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.ready).toBe(false);
    expect(result.current.startHref).toBe("/agents");
  });

  it("routes to /missions when an agent exists but no mission is available", async () => {
    server.use(
      http.get("*/api/system", () =>
        HttpResponse.json({
          role: "all",
          planes: [{ name: "docker", ok: true, detail: "ok", location: "local daemon" }],
          db_schema: "head",
          catalog: { state: "disconnected", message: null, last_sync: null },
          remotes: [],
          home: "/h",
          db_url: "sqlite:///x",
          topology: "local",
        }),
      ),
      http.get("*/api/agents", () => HttpResponse.json([agentFixture()])),
      http.get("*/api/missions", () =>
        HttpResponse.json([missionFixture({ installed: false })]),
      ),
    );
    const { result } = renderHook(() => useReadiness(), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.missionOk).toBe(false);
    expect(result.current.ready).toBe(false);
    expect(result.current.startHref).toBe("/missions");
  });

  it("routes to /setup when agent + mission are present but docker is down", async () => {
    server.use(
      // Docker plane down → infra gate fails; catalog connected satisfies missionOk.
      http.get("*/api/system", () =>
        HttpResponse.json({
          role: "all",
          planes: [{ name: "docker", ok: false, detail: "unreachable", location: "local daemon" }],
          db_schema: "head",
          catalog: { state: "connected", message: null, last_sync: null },
          remotes: [],
          home: "/h",
          db_url: "sqlite:///x",
          topology: "local",
        }),
      ),
      http.get("*/api/agents", () => HttpResponse.json([agentFixture()])),
      http.get("*/api/missions", () => HttpResponse.json([])),
    );
    const { result } = renderHook(() => useReadiness(), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.dockerOk).toBe(false);
    expect(result.current.agentOk).toBe(true);
    expect(result.current.missionOk).toBe(true);
    expect(result.current.ready).toBe(false);
    expect(result.current.startHref).toBe("/setup");
  });
});
