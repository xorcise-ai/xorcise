import { describe, it, expect, afterEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { api } from "./client";

afterEach(() => {
  delete window.__XORCISE_API_BASE__;
});

describe("location transparency", () => {
  it("targets a REMOTE server when the runtime base is overridden (no co-location assumption)", async () => {
    window.__XORCISE_API_BASE__ = "http://remote-server:3001/api";
    server.use(
      http.get("http://remote-server:3001/api/runs", () =>
        HttpResponse.json([{ run_id: "remote-run" }]),
      ),
    );
    const runs = await api.get<{ run_id: string }[]>("/runs");
    expect(runs[0].run_id).toBe("remote-run");
  });
});
