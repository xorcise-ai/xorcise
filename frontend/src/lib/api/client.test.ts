import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { api, ApiError, errorDetail } from "./client";

describe("api client", () => {
  it("GET resolves JSON against the runtime base", async () => {
    const agents = await api.get<unknown[]>("/agents");
    expect(Array.isArray(agents)).toBe(true);
  });

  it("POST sends a JSON body and returns the parsed response", async () => {
    server.use(
      http.post("*/api/agents", async ({ request }) => {
        const body = (await request.json()) as { name: string };
        return HttpResponse.json({ id: "a1", name: body.name }, { status: 201 });
      }),
    );
    const created = await api.post<{ id: string; name: string }>("/agents", {
      name: "scout",
    });
    expect(created).toEqual({ id: "a1", name: "scout" });
  });

  it("throws ApiError carrying the status on a 404", async () => {
    await expect(api.get("/runs/does-not-exist/result")).rejects.toBeInstanceOf(
      ApiError,
    );
    await expect(
      api.get("/runs/does-not-exist/result"),
    ).rejects.toMatchObject({ status: 404 });
  });
  it("errorDetail surfaces the server's own explanation of a failure", () => {
    const err = new ApiError("POST /runs → 503", 503, {
      detail: "Headscale control plane 'headscale' is not reachable",
    });
    expect(errorDetail(err)).toBe(
      "Headscale control plane 'headscale' is not reachable",
    );
  });

  it("errorDetail returns null rather than leaking wire trivia", () => {
    // No detail, a non-object body, a blank detail and a plain Error must all read as
    // "the server said nothing" — never as the technical "POST /x → 503" message.
    expect(errorDetail(new ApiError("POST /runs → 500", 500))).toBeNull();
    expect(errorDetail(new ApiError("m", 500, "boom"))).toBeNull();
    expect(errorDetail(new ApiError("m", 500, { detail: "   " }))).toBeNull();
    expect(errorDetail(new Error("network down"))).toBeNull();
  });
});
