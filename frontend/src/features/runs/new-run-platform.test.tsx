import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { agentFixture, missionFixture } from "@/test/fixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { NewRunForm } from "./new-run-form";

/** Platform honesty at the point of commitment (AS4/PS1): warn on emulation, block — with the
 *  reason — what the server would refuse anyway (base mismatch, no executable platform). */

const ARM_HOST = { host_platform: "linux/arm64" };

function wire(mission: ReturnType<typeof missionFixture>, system: object | null = ARM_HOST) {
  server.use(
    http.get("*/api/agents", () => HttpResponse.json([agentFixture({ name: "scout" })])),
    http.get("*/api/missions", () => HttpResponse.json([mission])),
    http.get("*/api/missions/pull-jobs", () => HttpResponse.json(null)),
    ...(system
      ? [http.get("*/api/system", () => HttpResponse.json(system))]
      : []),
  );
}

beforeEach(() => vi.clearAllMocks());

describe("NewRunForm platform honesty", () => {
  it("warns before pulling an amd64-only mission on an arm64 host — without blocking", async () => {
    wire(
      missionFixture({
        mission_id: "breachpoint",
        name: "BreachPoint",
        source: "library",
        installed: false,
        platforms: ["linux/amd64"],
      }),
    );
    renderWithProviders(<NewRunForm initialAgent="scout" initialMission="breachpoint" />);
    const warning = await screen.findByTestId("platform-warning");
    expect(warning).toHaveTextContent(/no native arm64 image/i);
    expect(warning).toHaveTextContent(/emulation/i);
    expect(await screen.findByRole("button", { name: /download & start run/i })).toBeEnabled();
  });

  it("warns when the INSTALLED mission runs emulated here", async () => {
    wire(
      missionFixture({
        mission_id: "breachpoint",
        name: "BreachPoint",
        source: "library",
        installed: true,
        platforms: ["linux/amd64"],
        platform: "linux/amd64",
        emulated: true,
      }),
    );
    renderWithProviders(<NewRunForm initialAgent="scout" initialMission="breachpoint" />);
    const warning = await screen.findByTestId("platform-warning");
    expect(warning).toHaveTextContent(/emulation layer/i);
    expect(await screen.findByRole("button", { name: /start run/i })).toBeEnabled();
  });

  it("stays silent when nothing is known (pre-contract catalog, no daemon)", async () => {
    wire(
      missionFixture({
        mission_id: "legacy",
        name: "Legacy",
        source: "library",
        installed: true,
        platforms: [],
        platform: null,
        emulated: null,
      }),
      { host_platform: null },
    );
    renderWithProviders(<NewRunForm initialAgent="scout" initialMission="legacy" />);
    expect(await screen.findByRole("button", { name: /start run/i })).toBeEnabled();
    expect(screen.queryByTestId("platform-warning")).not.toBeInTheDocument();
  });

  it("disables Start — with the reason — for a base-incompatible mission", async () => {
    wire(
      missionFixture({
        mission_id: "old-one",
        name: "Old One",
        source: "library",
        installed: true,
        compatible: false,
        compat_hint: "Update this mission to get the current base.",
      }),
    );
    renderWithProviders(<NewRunForm initialAgent="scout" initialMission="old-one" />);
    const blocked = await screen.findByTestId("incompatible-blocked");
    expect(blocked).toHaveTextContent(/not runnable on this XORCISE/i);
    expect(blocked).toHaveTextContent(/update this mission/i);
    expect(await screen.findByRole("button", { name: /start run/i })).toBeDisabled();
  });

  it("disables Start when the host can execute none of the validated platforms", async () => {
    wire(
      missionFixture({
        mission_id: "riscv-lab",
        name: "RiscV Lab",
        source: "library",
        installed: false,
        platforms: ["linux/riscv64"],
      }),
    );
    renderWithProviders(<NewRunForm initialAgent="scout" initialMission="riscv-lab" />);
    const blocked = await screen.findByTestId("no-platform-blocked");
    expect(blocked).toHaveTextContent(/no image this host can execute/i);
    expect(await screen.findByRole("button", { name: /start run/i })).toBeDisabled();
  });
});
