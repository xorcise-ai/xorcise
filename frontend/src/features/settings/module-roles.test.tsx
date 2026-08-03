import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/render";
import { StatusBar } from "@/components/layout/status-bar";
import { SettingsView } from "./settings-view";
import {
  groupByRole,
  deployedPlanes,
  roleAddress,
  roleTooltip,
  visibleModules,
} from "./module-groups";

const plane = (
  name: string,
  role: string,
  label: string,
  state: "ok" | "down" | "not_deployed",
  location = "",
) => ({ name, role, label, state, ok: state === "ok", detail: state === "ok" ? "ok" : "", location });

const ALL_HEALTHY = [
  plane("rest", "control", "REST API", "ok", "127.0.0.1:3001"),
  plane("docker", "runner", "Docker", "ok", "local daemon"),
  plane("headscale", "headscale", "Headscale", "ok", "headscale"),
  plane("otlp", "collector", "OTLP receiver", "ok", "127.0.0.1:4318"),
];

// A control-only host: it runs the API, and genuinely does not run the other three.
const CONTROL_ONLY = [
  plane("rest", "control", "REST API", "ok", "127.0.0.1:3001"),
  plane("docker", "runner", "Docker", "not_deployed"),
  plane("headscale", "headscale", "Headscale", "not_deployed"),
  plane("otlp", "collector", "OTLP receiver", "not_deployed"),
];

const systemWith = (planes: unknown[], role = "all") => ({
  role,
  planes,
  db_schema: "head",
  catalog: { state: "connected", message: null, last_sync: null },
  remotes: [],
  home: "/home/u/.xorcise",
  db_url: "sqlite:////home/u/.xorcise/xorcise.db",
  topology: "local",
});

describe("module → role grouping", () => {
  it("groups modules under the role that owns them, in request order", () => {
    const groups = groupByRole(ALL_HEALTHY as never);
    expect(groups.map((g) => g.role)).toEqual([
      "control",
      "runner",
      "headscale",
      "collector",
    ]);
    expect(groups[0].modules.map((m) => m.name)).toEqual(["rest"]);
  });

  it("a single unreachable module makes its whole role group down", () => {
    // Synthetic pair: since the MCP server was removed every role owns exactly one module, so
    // no real fixture exercises `worst()` across siblings. The rule has to survive the next
    // role that gains a second one — one red module must colour the whole group.
    const degraded = [
      plane("rest", "control", "REST API", "ok"),
      plane("extra", "control", "Extra", "down"),
    ];
    expect(groupByRole(degraded as never)[0].state).toBe("down");
  });

  it("a role with nothing deployed here reads absent, not broken", () => {
    const groups = groupByRole(CONTROL_ONLY as never);
    const byRole = Object.fromEntries(groups.map((g) => [g.role, g.state]));
    expect(byRole.control).toBe("ok");
    expect(byRole.runner).toBe("not_deployed");
    expect(byRole.collector).toBe("not_deployed");
  });

  it("the hover text names the modules and addresses behind the verdict", () => {
    const control = groupByRole(ALL_HEALTHY as never)[0];
    const tip = roleTooltip(control);
    expect(tip).toContain("connected");
    expect(tip).toContain("REST API");
    expect(tip).toContain("127.0.0.1:3001");
  });

  it("still groups a module whose role field is missing", () => {
    // A server older than the `role` field (or any fixture that omits it) must not make modules
    // VANISH from the health view — silently dropping a row is worse than mislabelling one.
    // Caught for real: the pre-existing settings fixture has no `role`, and strict grouping
    // rendered an empty Modules card.
    const legacy = [
      { name: "rest", ok: true, detail: "ok", location: "127.0.0.1:3001" },
      { name: "docker", ok: false, detail: "missing", location: "local daemon" },
    ];
    const groups = groupByRole(legacy as never);
    expect(groups.map((g) => g.role)).toEqual(["control", "runner"]);
    expect(groups[1].state).toBe("down");
  });

  it("deployedPlanes excludes not_deployed so health counts stay honest", () => {
    // The regression this guards: `not_deployed` carries ok:false, so a naive
    // planes.filter(p => p.ok).length === planes.length turned a correctly-configured
    // control-only host into "1/4 modules healthy" with a red tile.
    const deployed = deployedPlanes(CONTROL_ONLY as never);
    expect(deployed).toHaveLength(1);
    expect(deployed.every((p) => p.ok)).toBe(true);
  });
});

describe("StatusBar", () => {
  it("shows a dot per service role once the probe answers", async () => {
    server.use(http.get("*/api/system", () => HttpResponse.json(systemWith(ALL_HEALTHY))));
    renderWithProviders(<StatusBar healthy />);

    expect(await screen.findByLabelText(/^Control — connected/s)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Runner — connected/s)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Headscale — connected/s)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Collector — connected/s)).toBeInTheDocument();
  });

  it("says not-on-this-host rather than unreachable for an absent role", async () => {
    server.use(
      http.get("*/api/system", () => HttpResponse.json(systemWith(CONTROL_ONLY, "control"))),
    );
    renderWithProviders(<StatusBar healthy />);

    expect(await screen.findByLabelText(/^Collector — not on this host/s)).toBeInTheDocument();
  });

  it("claims nothing about roles while the server is unreachable", () => {
    server.use(http.get("*/api/system", () => HttpResponse.error()));
    renderWithProviders(<StatusBar healthy={false} />);

    expect(screen.getByText(/xorcise\.core unreachable/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Control —/s)).not.toBeInTheDocument();
  });
});

describe("Settings — Modules card", () => {
  it("groups the module rows under their role headings", async () => {
    server.use(
      http.get("*/api/config", () =>
        HttpResponse.json({
          judge: { configured: false },
          terrain: { configured: false, uses_judge_default: true },
          default_budget_seconds: 3600,
          catalog: { connected: true, url: "https://x" },
          network: { headscale_url: null, advertise_host: null },
        }),
      ),
      http.get("*/api/system", () => HttpResponse.json(systemWith(ALL_HEALTHY))),
    );
    renderWithProviders(<SettingsView />);

    // Role headings, and the human module names (not the raw plane keys).
    expect(await screen.findByText("Collector")).toBeInTheDocument();
    expect(screen.getByText("OTLP receiver")).toBeInTheDocument();
    expect(screen.getByText("REST API")).toBeInTheDocument();
  });

  it("renders every group in ONE table so the columns line up", async () => {
    server.use(
      http.get("*/api/config", () =>
        HttpResponse.json({
          judge: { configured: false },
          terrain: { configured: false, uses_judge_default: true },
          default_budget_seconds: 3600,
          catalog: { connected: true, url: "https://x" },
          network: { headscale_url: null, advertise_host: null },
        }),
      ),
      http.get("*/api/system", () => HttpResponse.json(systemWith(ALL_HEALTHY))),
    );
    const { container } = renderWithProviders(<SettingsView />);
    await screen.findByText("Collector");

    // A table per group let each compute its own column widths, so "Healthy" landed at a
    // different x in every block and the card read as ragged. One table, one <colgroup>.
    const moduleTable = container.querySelector("table:has(colgroup)");
    expect(moduleTable).not.toBeNull();
    // All four role headings and all five module rows live in that single table.
    for (const role of ["Control", "Runner", "Headscale", "Collector"]) {
      expect(moduleTable?.textContent).toContain(role);
    }
    expect(moduleTable?.querySelectorAll("colgroup")).toHaveLength(1);
  });

  it("keeps the Modules card free of any multi-machine editor", async () => {
    server.use(
      http.get("*/api/config", () =>
        HttpResponse.json({
          judge: { configured: false },
          terrain: { configured: false, uses_judge_default: true },
          default_budget_seconds: 3600,
          catalog: { connected: true, url: "https://x" },
          network: { headscale_url: null, advertise_host: null },
        }),
      ),
      http.get("*/api/system", () => HttpResponse.json(systemWith(ALL_HEALTHY))),
    );
    renderWithProviders(<SettingsView />);

    // The Modules card reports where Headscale IS — a probe result. That is the whole of the
    // GUI's business with multi-machine addresses; changing them is CLI-only, because the
    // withdrawn editor mostly produced a server that could not start a run. The Distributed
    // deployment card exists again as a preview, and must not have brought the editor back
    // with it.
    expect(await screen.findByText("Headscale")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^edit$/i })).not.toBeInTheDocument();
  });

  it("shows a suppressed module's row the moment it stops being healthy", async () => {
    const withBrokenHeadscale = ALL_HEALTHY.map((p) =>
      p.name === "headscale" ? { ...p, state: "down", ok: false, detail: "unreachable" } : p,
    );
    server.use(
      http.get("*/api/config", () =>
        HttpResponse.json({
          judge: { configured: false },
          terrain: { configured: false, uses_judge_default: true },
          default_budget_seconds: 3600,
          catalog: { connected: true, url: "https://x" },
          network: { headscale_url: null, advertise_host: null },
        }),
      ),
      http.get("*/api/system", () => HttpResponse.json(systemWith(withBrokenHeadscale))),
    );
    renderWithProviders(<SettingsView />);

    // A red role dot must always have a visible cause — suppressing a row that merely
    // restates its role is fine, suppressing a broken one would leave the operator with a
    // warning and nothing to read.
    expect(await screen.findByText("unreachable")).toBeInTheDocument();
  });
});

describe("module row suppression", () => {
  it("hides only a role-restating child, keeps every other row", () => {
    const [control, runner, headscale, collector] = groupByRole(ALL_HEALTHY as never);
    // Control/Runner/Collector: the module name says something the role does not.
    expect(visibleModules(control).map((m) => m.name)).toEqual(["rest"]);
    expect(visibleModules(runner).map((m) => m.name)).toEqual(["docker"]);
    expect(visibleModules(collector).map((m) => m.name)).toEqual(["otlp"]);
    // Headscale under HEADSCALE said nothing twice: the heading takes over its address.
    expect(visibleModules(headscale)).toEqual([]);
    expect(roleAddress(headscale)).toBe("headscale");
  });

  it("surfaces a suppressed module the moment it stops being healthy", () => {
    const broken = ALL_HEALTHY.map((p) =>
      p.name === "headscale" ? { ...p, state: "down", ok: false } : p,
    );
    const headscale = groupByRole(broken as never)[2];
    // Healthy it was folded into the heading; unhealthy it must have its own visible row.
    expect(visibleModules(headscale).map((m) => m.name)).toEqual(["headscale"]);
  });
});
