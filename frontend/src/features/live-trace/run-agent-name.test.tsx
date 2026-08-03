import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { runFixture, agentFixture } from "@/test/fixtures";

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

import { RunLive } from "./run-live";

describe("RunLive agent identity", () => {
  it("shows the agent name in the meta, not the raw id", async () => {
    server.use(
      http.get("*/api/runs", () =>
        HttpResponse.json([
          runFixture({
            run_id: "r1",
            agent_id: "1c9343952bf64ac6b92785daeda54e1c",
            state: "active",
          }),
        ]),
      ),
      http.get("*/api/agents", () =>
        HttpResponse.json([
          agentFixture({ id: "1c9343952bf64ac6b92785daeda54e1c", name: "scout" }),
        ]),
      ),
    );
    renderWithProviders(<RunLive runId="r1" />);
    // The Agent cell shows the resolved name (with its pinned version), never the raw id.
    expect(await screen.findByText(/^scout v\d+$/)).toBeInTheDocument();
    expect(screen.queryByText(/1c9343952bf64ac6b92785daeda54e1c/)).not.toBeInTheDocument();
  });
});
