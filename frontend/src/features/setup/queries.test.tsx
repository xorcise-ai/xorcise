import { describe, it, expect } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { createQueryWrapper } from "@/test/render";
import { useServerHealth } from "./queries";

describe("useServerHealth", () => {
  it("reports ok when the server is up", async () => {
    const { result } = renderHook(() => useServerHealth(), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.data?.status).toBe("ok"));
    expect(result.current.isError).toBe(false);
  });

  it("reports an error when the server is unreachable", async () => {
    server.use(
      http.get("*/api/health", () =>
        HttpResponse.json({ detail: "down" }, { status: 503 }),
      ),
    );
    const { result } = renderHook(() => useServerHealth(), {
      wrapper: createQueryWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
