import type { PlaneStatus } from "@/lib/api/types";

/**
 * Modules grouped by the SERVICE ROLE that owns them — the shape both the status bar and the
 * Settings Modules card render.
 *
 * XORCISE is built as four roles (`xorcise serve --role <key>`), and today a default install
 * collapses all of them into one process. Grouping the probes by role is what makes the
 * single-host case and the future multi-host case read the same: when a module moves to
 * another machine only its `location` changes, not this structure.
 */
export type ModuleState = "ok" | "down" | "not_deployed";

/** Role order is deliberate: the request path — control → runner → headscale → collector. */
export const ROLE_ORDER = ["control", "runner", "headscale", "collector"] as const;
export type RoleKey = (typeof ROLE_ORDER)[number];

export const ROLE_LABELS: Record<RoleKey, string> = {
  control: "Control",
  runner: "Runner",
  headscale: "Headscale",
  collector: "Collector",
};

export interface RoleGroup {
  role: RoleKey;
  label: string;
  modules: PlaneStatus[];
  /** Worst state across the group — one dot has to answer for all of it. */
  state: ModuleState;
}

/**
 * Which role owns a module, by its stable plane key. This mirrors the server's `_OWNER` map and
 * exists ONLY as a fallback: `role` is populated by any current server, but if it ever arrives
 * empty — an older server, a hand-rolled fixture — grouping strictly by the field would DROP the
 * module from the card entirely. A module silently disappearing from a health view is a worse
 * failure than a mislabelled one.
 */
const ROLE_BY_PLANE: Record<string, RoleKey> = {
  rest: "control",
  docker: "runner",
  headscale: "headscale",
  otlp: "collector",
};

function ownerRole(p: PlaneStatus): RoleKey | null {
  const declared = (p as { role?: string }).role;
  if (declared && (ROLE_ORDER as readonly string[]).includes(declared)) {
    return declared as RoleKey;
  }
  return ROLE_BY_PLANE[p.name] ?? null;
}

/** A plane's state, tolerating an older server that predates the `state` field. */
export function planeState(p: PlaneStatus): ModuleState {
  const s = (p as { state?: string }).state;
  if (s === "ok" || s === "down" || s === "not_deployed") return s;
  return p.ok ? "ok" : "down";
}

/** The human name for a module, falling back to its plane key. */
export function moduleLabel(p: PlaneStatus): string {
  return (p as { label?: string }).label || p.name;
}

/**
 * Collapse a group to ONE state. `down` wins over everything: a single unreachable module is
 * the thing the operator has to know about. A group with nothing deployed here is
 * `not_deployed` — absent, not broken — so a correctly-configured host is never painted red
 * for the modules it was never meant to run.
 */
function worst(modules: PlaneStatus[]): ModuleState {
  const states = modules.map(planeState);
  if (states.includes("down")) return "down";
  if (states.length > 0 && states.every((s) => s === "not_deployed")) return "not_deployed";
  return "ok";
}

/**
 * Only the modules this host is actually supposed to run.
 *
 * Every "N/M modules healthy" readout MUST count against this, not the raw list: a
 * `not_deployed` module carries `ok: false` (it is not up), so counting it as a failure would
 * report a correctly-configured control-only host as "2/5 modules healthy" and turn its health
 * tile red. Absent is not unhealthy.
 */
export function deployedPlanes(planes: PlaneStatus[]): PlaneStatus[] {
  return planes.filter((p) => planeState(p) !== "not_deployed");
}

/** Group the flat `planes` list by owning role, dropping roles with no modules. */
export function groupByRole(planes: PlaneStatus[]): RoleGroup[] {
  return ROLE_ORDER.map((role) => {
    const modules = planes.filter((p) => ownerRole(p) === role);
    return { role, label: ROLE_LABELS[role], modules, state: worst(modules) };
  }).filter((g) => g.modules.length > 0);
}

/**
 * The child rows to render under a role heading.
 *
 * One thing is suppressed, and only while healthy: a lone module whose name merely restates its
 * role — "Headscale" under HEADSCALE is a row that says nothing twice. When a role has no
 * visible children the heading itself carries the state and address (see roleAddress), so
 * nothing is lost.
 *
 * There was also a QUIET_WHEN_HEALTHY set, holding only "mcp" — the MCP server was an
 * implementation detail of the control plane that an operator never acted on. The MCP server no
 * longer exists, so the set went with it rather than staying behind as an empty abstraction.
 */
export function visibleModules(group: RoleGroup): PlaneStatus[] {
  return group.modules.filter((m) => {
    if (planeState(m) !== "ok") return true; // never hide a problem
    const restatesRole =
      group.modules.length === 1 &&
      moduleLabel(m).toLowerCase() === group.label.toLowerCase();
    return !restatesRole;
  });
}

/** Address for the role heading when it stands in for its only module ("" otherwise). */
export function roleAddress(group: RoleGroup): string {
  if (visibleModules(group).length > 0) return "";
  return group.modules
    .map((m) => m.location)
    .filter(Boolean)
    .join(" · ");
}

/** One module's state as a phrase, e.g. "Healthy", "Not on this host", "unreachable". */
export function moduleStateLabel(p: PlaneStatus): string {
  const state = planeState(p);
  if (state === "ok") return "Healthy";
  if (state === "not_deployed") return "Not on this host";
  return p.detail || "Down";
}

/**
 * The hover text for a role's dot: what the role is, whether it is reachable, and which
 * concrete modules and addresses that verdict came from — so "connected" is never a bare
 * claim the operator has to take on trust.
 */
export function roleTooltip(group: RoleGroup): string {
  const verdict =
    group.state === "ok"
      ? "connected"
      : group.state === "not_deployed"
        ? "not on this host"
        : "unreachable";
  const parts = group.modules.map((m) => {
    const where = m.location ? ` (${m.location})` : "";
    return `${moduleLabel(m)}: ${moduleStateLabel(m)}${where}`;
  });
  return `${group.label} — ${verdict}\n${parts.join("\n")}`;
}
