"""Run-create coordination (delivery/rest layer).

Lives in rest — the layer above the domain modules — because creating a run coordinates
sibling modules (agents gate, runs persistence) plus the part-islands via the orchestration
ports (ControlPort, NetworkFencePort) and the missions install store. orchestration
cannot host this: it is a same-layer sibling of runs/agents (.importlinter layers).
Pure + injectable: tests drive it with stub ports; CLI reaches it over REST.
"""

from __future__ import annotations

import logging
import shlex
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from xorcise.core import agents, runs
from xorcise.core.config import Settings
from xorcise.core.contracts.connect import (
    ConnectArtifact,
    ConnectAttachment,
    ConnectTarget,
    LaunchProfile,
    MissionPrompt,
    StartupTips,
)
from xorcise.core.contracts.control import DeployRequest, MissionRef, NetworkSpec
from xorcise.core.contracts.run import RunEntry
from xorcise.core.missions import get_installed
from xorcise.core.rest.mission_pull import PullDeps, build_pull_deps, pull_mission
from xorcise.core.runcontrol.intel_policy import allowed_intel
from xorcise.core.runs.observed import (
    InMemoryObservedFactsStore,
    ObservedFactsStore,
    SqliteObservedFactsStore,
    network_facts,
)
from xorcise.core.runs.prompt import (
    assemble_mission_prompt,
    rebase_run_control_host,
    render_prompt_text,
)

if TYPE_CHECKING:
    # Type-only: the ports ABCs transitively reference the headscale plane. Importing
    # them eagerly would drag the part-islands into the control plane at boot, breaking
    # role isolation (tests/topology::test_role_boots_only_its_plane). The concrete
    # part-island imports stay lazy, inside the functions that actually wire them.
    from xorcise.core.contracts.agent import AgentEntry
    from xorcise.core.headscale import HeadscaleCli, RunNetwork
    from xorcise.core.missions.runtime import InstalledMission
    from xorcise.core.orchestration.ports import ControlPort, NetworkFencePort
    from xorcise.core.runs.launch.base import HarnessLaunchProvider


log = logging.getLogger(__name__)


class RunCreateError(Exception):
    """Base for run-create gate failures."""


class NoAgentError(RunCreateError):
    """No registered agent matches the requested name (the registered-agent gate)."""


class MissionNotInstalledError(RunCreateError):
    """The requested mission is not installed.

    Retained for back-compat; run-create now auto-pulls on demand, so an
    unobtainable mission surfaces as MissionNotInCatalogError/PullError instead."""


def _no_live_subnets() -> set[str]:
    """Default `live_subnets`: no Docker to enumerate (stub / unit path), so no leftover subnets."""
    return set()


def _no_nested_check() -> None:
    """Default `require_nested`: the stub/unit path deploys nothing, so there is nothing to nest."""


def _no_base_check(image_ref: str) -> None:
    """Default `check_base_compat`: the stub/unit path deploys no real image to gate."""


@dataclass(frozen=True)
class RunCreateDeps:
    control: ControlPort
    fence: NetworkFencePort
    api_key: str
    install_root: Path
    login_server: str
    base_network: str
    cidr_prefix: int
    default_budget: int
    pull: PullDeps
    ca_cert: str = ""  # PEM of the Headscale TLS CA the router trusts (air-gapped)
    extra_hosts: tuple[str, ...] = ()  # router extra_hosts, e.g. "headscale.local:<ip>"
    advertise_host: str = ""  # host reachable from the agent container (OTLP endpoint)
    # The server's own loopback bind host (settings.host) — where a HOST-launched harness (Claude
    # Code, `claude -p`) reaches run-control, since it can't resolve the container-only
    # advertise_host (host.docker.internal). Empty falls back to advertise_host.
    host: str = ""
    otlp_port: int = 4318  # OTLP HTTP port on the collector
    rest_port: int = 3001  # server REST port the agent reaches run-control on
    # where per-run observed facts (network/ACL boundary config) are recorded. Defaults
    # to in-memory so test constructions need not supply it; boot wires the Sqlite store.
    observed: ObservedFactsStore = field(default_factory=InMemoryObservedFactsStore)
    # The /prefix subnets to EXCLUDE from allocation because a live Docker network already holds
    # them — including LEFTOVER networks the run DB no longer tracks (imperfect teardown). Unioned
    # into the allocator so a stale subnet is never re-handed to a new run (the collision that made
    # a mission deploy fail, leaving the agent joined-but-target-dead). Empty by default (stub /
    # unit path with no Docker); boot wires the docker-backed lister.
    live_subnets: Callable[[], set[str]] = _no_live_subnets
    # Raises NestedContainersUnavailableError if this host cannot run a mission's containers
    # inside the run container — the only supported topology. Injected (not called inline) so the
    # stub/unit path has nothing to probe and no Docker to reach; boot wires the real check.
    require_nested: Callable[[], None] = _no_nested_check
    # Raises BaseImageIncompatibleError if the mission's fused image was built on a base
    # generation this XORCISE cannot run. Takes the image ref; boot wires label+tag inspection.
    check_base_compat: Callable[[str], None] = _no_base_check


def _use_real_headscale(settings: Settings) -> bool:
    """Real fence iff this process owns the Headscale plane and stubs aren't forced."""
    return not settings.use_stubs and settings.role in {"all", "runner"}


def _probe_headscale(cli: HeadscaleCli, container: str) -> None:
    """Fail loud with a remediation if the Headscale control plane is unreachable.

    Mirrors the Docker fail-loud path: the remediation (which names XORCISE_USE_STUBS) lives here
    in the rest layer, not in the headscale part-island. Without this, a missing/stopped container
    surfaces as a bare HeadscaleError ('No such container') deep inside create_run."""
    from xorcise.core.headscale import HeadscaleError

    try:
        cli.version()
    except HeadscaleError as exc:
        raise RuntimeError(
            f"Headscale control plane {container!r} is not reachable — run 'xorcise up' "
            "to provision a local one, or set XORCISE_USE_STUBS=1"
        ) from exc


def _real_headscale_cli(settings: Settings) -> HeadscaleCli:
    from xorcise.core.headscale import DockerExecHeadscaleCli

    cli = DockerExecHeadscaleCli(settings.headscale_container)
    _probe_headscale(cli, settings.headscale_container)
    return cli


def build_run_create_deps(settings: Settings, *, use_docker: bool | None = None) -> RunCreateDeps:
    """Wire the in-process run-create deps from settings.

    Real-by-default: `use_docker=None` derives from settings — real DockerSdkDriver-backed
    control (`_use_real_docker`) and the real DockerExecHeadscaleCli fence
    (`_use_real_headscale`) on a Docker/Headscale-owning role (`all`/`runner`), stub
    when `use_stubs` is set or the role doesn't own the plane. An explicit `use_docker` bool
    overrides both (tests inject False; the integration loop passes True).
    """
    # Lazy by design: wiring the concrete adapters is the ONE place the server touches the
    # headscale/runner planes. Keeping these out of module scope lets the control plane
    # import this module at boot without leaking those planes (role isolation).
    from xorcise.core.headscale import NetworkController, StubHeadscaleCli
    from xorcise.core.orchestration.clients.control import (
        InProcessControlStub,
        RunnerControlAdapter,
    )
    from xorcise.core.orchestration.clients.headscale_client import HeadscaleFenceClient
    from xorcise.core.rest.mission_pull import _real_docker_driver, _use_real_docker

    control_real = _use_real_docker(settings) if use_docker is None else use_docker
    fence_real = _use_real_headscale(settings) if use_docker is None else use_docker

    # Topology-resolved agent-facing host: local uses the docker host-gateway alias so
    # the agent reaches the COLLECTOR (OTLP) without the tailnet; distributed uses the advertised
    # tailnet host. Used ONLY for the OTLP endpoint (deps.advertise_host).
    local = settings.deployment_topology == "local"
    agent_host = _agent_facing_host(settings)
    # The tailnet control plane — the Headscale login server the per-run ROUTER dials
    # (NetworkSpec.login_server) and the agent's `tailscale up --login-server` join recipe
    # (mission.login_server) — ALWAYS uses the advertised tailnet host, NEVER host.docker.internal:
    # the per-run router container gets no host-gateway mapping and cannot resolve that alias.
    # This is topology-independent (the mission data plane is unchanged).
    tailnet_host = settings.headscale_advertise_host or settings.host
    login_server = settings.headscale_url or f"http://{tailnet_host}:{settings.headscale_port}"
    cli = _real_headscale_cli(settings) if fence_real else StubHeadscaleCli()
    fence = HeadscaleFenceClient(
        NetworkController(
            cli,
            router_tag=settings.router_tag,
            orchestrator_user=settings.orchestrator_user,
            key_expiration=settings.key_expiration,
            # local reaches the collector directly via docker — no tailnet carve-out
            collector_addr=settings.headscale_advertise_host if (fence_real and not local) else "",
            collector_port=settings.otlp_port,
            # render the ACL from persisted non-terminal runs so no process clobbers
            # another's rules. Real fence only — the stub keeps its in-process view for unit tests.
            active_provider=_acl_active_provider if fence_real else None,
        )
    )
    control: ControlPort
    # Default: no Docker to enumerate (stub/unit path) → the allocator sees no leftover subnets.
    live_subnets: Callable[[], set[str]] = _no_live_subnets
    # Default: the stub path deploys nothing, so nesting/base compat are not preconditions for it.
    require_nested: Callable[[], None] = _no_nested_check
    check_base_compat: Callable[[str], None] = _no_base_check
    if control_real:
        # Real runner: _real_docker_driver fails loud if Docker/the runner extra is absent.
        from xorcise.core.headscale import overlapping_subnets
        from xorcise.core.runner.service import RunnerControlService

        driver = _real_docker_driver()
        control = RunnerControlAdapter(
            RunnerControlService(driver, login_server=login_server),
            api_key="local",
        )
        # Reconcile allocation against live Docker networks (incl. LEFTOVERS the DB no longer
        # tracks), filtered to the run pool so Docker's own bridges never mask a run subnet.
        base, prefix = settings.base_network, settings.cidr_prefix

        def live_subnets() -> set[str]:
            return overlapping_subnets(base, prefix, driver.list_network_cidrs())

        # The mission stack only ever runs nested, so verify the host can do that before a run
        # commits to anything. Memoised in-process, so it costs ~nothing per run after the first
        # (which boot pre-warms — see role_all).
        from xorcise.core.rest.docker_runtime import (
            require_base_compatible,
            require_nested_support,
        )

        def _client() -> object:
            import docker  # type: ignore[import-untyped]

            try:
                return docker.from_env()
            except Exception as exc:  # daemon down / from_env failure — DockerException is NOT a
                # RuntimeError, so unwrapped it escapes the runs router's except clauses as a raw
                # 500; RuntimeError maps to a clean 503 with a remediation (mirrors mission_pull).
                raise RuntimeError(
                    "Docker daemon is not reachable — start Docker or set XORCISE_USE_STUBS=1"
                ) from exc

        def require_nested() -> None:
            require_nested_support(settings, _client)

        def check_base_compat(image_ref: str) -> None:
            require_base_compatible(image_ref, label_lookup=driver.image_labels)
    else:
        control = InProcessControlStub(api_key="local")
    ca_cert = Path(settings.headscale_ca_cert).read_text() if settings.headscale_ca_cert else ""
    extra_hosts = (settings.headscale_host_alias,) if settings.headscale_host_alias else ()
    return RunCreateDeps(
        control=control,
        fence=fence,
        api_key="local",
        install_root=Path(settings.missions_root),
        login_server=login_server,
        base_network=settings.base_network,
        cidr_prefix=settings.cidr_prefix,
        default_budget=settings.default_budget_seconds,
        pull=build_pull_deps(settings, use_docker=use_docker),
        ca_cert=ca_cert,
        extra_hosts=extra_hosts,
        advertise_host=agent_host,  # local => host.docker.internal; distributed => advertise_host
        host=settings.host,  # loopback bind — where a host-launched harness reaches run-control
        otlp_port=settings.otlp_port,
        rest_port=settings.rest_port,  # agent-facing run-control base URL port
        observed=SqliteObservedFactsStore(),  # persist observed facts run-scoped
        live_subnets=live_subnets,  # reconcile allocation against live Docker networks
        require_nested=require_nested,  # fail a lab run early if the host cannot nest containers
        check_base_compat=check_base_compat,  # refuse an artifact built on an incompatible base
    )


def _agent_facing_host(settings: Settings) -> str:
    """Host the agent uses to reach the controller + collector.

    local (default): the docker host-gateway alias, reachable from any docker
    network without the tailnet. distributed: the advertised tailnet
    host.
    """
    if settings.deployment_topology == "local":
        return "host.docker.internal"
    return settings.headscale_advertise_host or settings.host


def _otlp_endpoint(deps: RunCreateDeps) -> str:
    """Return the OTLP HTTP endpoint URL for *deps*, or empty string when unset.

    Behavior-preserving extraction of the inline expression inside create_run so callers
    can build the LaunchProfile independently and tests can assert on the assembled URL.
    """
    return f"http://{deps.advertise_host}:{deps.otlp_port}" if deps.advertise_host else ""


def launch_profile_for(
    settings: Settings,
    source_agent: str = "generic",
    run_id: str = "",
    launch_mode: str = "container",
) -> tuple[LaunchProfile, bool]:
    """The harness-facing LaunchProfile (OTel env) for a run under *settings* (harness-aware).

    Mirrors _otlp_endpoint(deps) but from settings alone — no deps build, so a read-only
    launch-profile GET never triggers the fail-loud Docker/Headscale probes in
    build_run_create_deps. The collector address is a deployment constant (agent-facing host +
    otlp_port), not per-run state. *source_agent* selects the telemetry provider (the emit-side
    mirror of the replay adapter); *run_id* lets a known harness stamp
    OTEL_RESOURCE_ATTRIBUTES=xorcise.run_id=<id> so every OTLP batch correlates.

    *launch_mode*: the agent-facing host defaults to the CONTAINER address (local
    topology → ``host.docker.internal``, reachable only from inside a container). A HOST-launched
    harness — e.g. ``claude -p`` in a terminal — cannot resolve that name, so its OTLP exporter
    silently fails and NO spans arrive. ``launch_mode="host"`` points the endpoint at the server's
    own loopback bind host (``settings.host``) so a host CLI reaches the collector. Returns
    (profile, fallback) — fallback=True when no provider matched source_agent (generic floor).
    """
    host = _agent_facing_host(settings)
    # A HOST-launched harness can't resolve the container-only agent-facing host; point it at the
    # server's own loopback bind host (settings.host) instead — config-driven, no literal.
    if launch_mode == "host" and host:
        host = settings.host
    endpoint = f"http://{host}:{settings.otlp_port}" if host else ""
    # Lazy: keep the concrete telemetry providers off role:control's boot path (plane isolation),
    # mirroring how the runs router lazy-imports the otel display plane. load_providers() registers
    # the providers (emit plane) via the harness_adapters tier; select() then reads the registry.
    from xorcise.core.harness_adapters import load_providers
    from xorcise.core.runs.telemetry.base import EmitContext
    from xorcise.core.runs.telemetry.registry import select

    load_providers()
    provider, fallback = select(source_agent)
    profile = provider.profile(
        EmitContext(run_id=run_id, otlp_endpoint=endpoint, source_agent=source_agent)
    )
    return profile, fallback


def build_startup_tips(
    profile: LaunchProfile,
    command_template: str | None,
    mission: str | None,
    tips: tuple[str, ...] = (),
    *,
    otlp_traces_endpoint: str = "",
    otlp_logs_endpoint: str = "",
) -> StartupTips:
    """Assemble copy-paste launch tips from a LaunchProfile + the harness's command template
    + per-harness launch tips.

    Pure + harness-agnostic. ``{mission}`` is filled with the shell-quoted mission, or a literal
    ``<mission>`` placeholder when the run has none. A harness that carries its OTLP exporter config
    in the command itself (codex, via ``-c`` flags — it ignores the OTEL_EXPORTER_* env) can also
    reference ``{otlp_traces_endpoint}`` / ``{otlp_logs_endpoint}``; both are filled here from the
    mode-aware per-signal collector URLs the caller computed. Templates that omit these placeholders
    (Claude Code) are unaffected — the extra keys are simply unused. ``shell_block`` =
    ``export K=<quoted V>`` lines then the command (omitted when the harness has no template)."""
    lines = [f"export {k}={shlex.quote(v)}" for k, v in profile.env]
    command: str | None = None
    if command_template:
        filled = shlex.quote(mission) if mission else "<mission>"
        command = command_template.format(
            mission=filled,
            otlp_traces_endpoint=otlp_traces_endpoint,
            otlp_logs_endpoint=otlp_logs_endpoint,
        )
        lines.append(command)
    return StartupTips(
        env=tuple(profile.env),
        command=command,
        shell_block="\n".join(lines),
        correlation=profile.correlation,
        notes=profile.notes,
        tips=tuple(tips),
    )


_LAUNCH_MODES = ("container", "host")


def _run_control_url(deps: RunCreateDeps, run_id: str, host: str | None = None) -> str:
    """The authenticated run-control base URL the agent calls.

    Mirrors _otlp_endpoint: the server's REST API on the agent-facing host — NOT the Headscale
    control plane (the old runner-service value pointed there, so a prompt-only agent fired at
    :8443 and 401'd). Path includes the /api mount + /runs prefix; the verbs hang off it.

    *host* overrides the reachable host: a HOST-launched harness (Claude Code) is given the
    server's loopback bind (deps.host) instead of the container-only advertise_host, so the
    persisted mission's run-control URL is reachable from where the agent actually runs. Defaults
    to advertise_host (the container/tailnet address) when unset.
    """
    reachable = host if host else deps.advertise_host
    if not reachable:
        return ""
    return f"http://{reachable}:{deps.rest_port}/api/runs/{run_id}"


def run_control_host_for_mode(settings: Settings, launch_mode: str) -> str:
    """The run-control host reachable from where a *launch_mode* agent runs.

    Mirrors launch_profile_for's collector-host choice so the run-control URL and the OTLP endpoint
    always name the SAME host: ``host`` mode → the server's own loopback bind (settings.host, what
    a terminal CLI reaches); ``container`` mode → the agent-facing/advertise host
    (host.docker.internal locally, resolvable only from inside a container)."""
    return settings.host if launch_mode == "host" else _agent_facing_host(settings)


def otlp_signal_endpoints(settings: Settings, launch_mode: str) -> tuple[str, str]:
    """The ``(traces, logs)`` OTLP/HTTP per-signal URLs for a *launch_mode* agent (codex).

    Full per-signal paths (``…/v1/traces``, ``…/v1/logs``) on the SAME mode-aware collector host as
    run-control and the OTLP env. For a harness like codex that carries its exporter config in the
    launch command's ``-c`` flags and does NOT append the ``/v1/<signal>`` path itself. Both empty
    when no collector host is configured."""
    host = run_control_host_for_mode(settings, launch_mode)
    if not host:
        return "", ""
    base = f"http://{host}:{settings.otlp_port}"
    return f"{base}/v1/traces", f"{base}/v1/logs"


def rebase_prompt_to_launch_mode(
    settings: Settings,
    *,
    launch: HarnessLaunchProvider,
    prompt: str | None,
    launch_mode: str,
    launch_modes: tuple[str, ...] | None = None,
) -> tuple[str | None, str]:
    """Clamp *launch_mode* to what *launch* supports and rebase *prompt*'s run-control host to it.

    Returns ``(rebased_prompt, effective_mode)``. The persisted mission prompt bakes ONE run-control
    host at create time — the container advertise host for a dual/generic harness, the loopback for
    a host-only harness (Claude Code). The GUI launch-mode toggle must move that host
    exactly as it already moves the collector env, so the whole hand-off is consistent and not just
    the telemetry block. Clamp mirrors run_launch_profile so the prompt and the profile agree on the
    effective mode. A no-op on the run-control host when the baked and requested modes coincide."""
    allowed_modes = launch_modes or launch.launch_modes
    mode = launch_mode if launch_mode in _LAUNCH_MODES else "container"
    if mode not in allowed_modes:
        mode = allowed_modes[0]
    if prompt is None:
        return None, mode
    baked_mode = "container" if "container" in allowed_modes else "host"
    rebased = rebase_run_control_host(
        prompt,
        from_host=run_control_host_for_mode(settings, baked_mode),
        to_host=run_control_host_for_mode(settings, mode),
    )
    return rebased, mode


# subnet allocation must avoid every live run's subnet AND be collision-safe across
# processes/crashes. The reservation is a DB row (runs.reserve_run) inserted BEFORE deploy — durable
# and visible to every process the instant it's taken — guarded by a partial unique index on
# network_cidr (non-terminal runs). This lock just serializes the fast allocate→insert within this
# process to avoid wasted retries; the index is the real cross-process guarantee.
_ALLOC_LOCK = threading.Lock()


def _reserve_run_subnet(
    deps: RunCreateDeps,
    run_id: str,
    agent_id: str,
    mission: str,
    entry_networks: tuple[str, ...],
    *,
    name: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Allocate a /prefix avoiding every live run's subnet and reserve it as a DB row.

    Returns (cidr, entry_subnets). Retries on the partial-unique-index collision — a concurrent
    cross-process reservation of the same subnet — picking the next free one. The reserved row is
    state 'created' with network_cidr + entry_cidrs set; finalize_run fills the rest and delete_run
    releases it if the create fails before finalize."""
    from sqlalchemy.exc import IntegrityError

    from xorcise.core.headscale import allocate_cidr  # lazy: keep headscale off the boot path
    from xorcise.core.runner.netoverride import carve_entry_subnets

    with _ALLOC_LOCK:
        taken: set[str] = set()
        # Subnets a live Docker network already holds — including LEFTOVER networks the DB no longer
        # tracks (imperfect teardown). Enumerated ONCE per reservation: unioned in so the allocator
        # never re-hands a stale subnet, which would collide the new mission's compose network.
        leftover = deps.live_subnets()
        while True:
            cidr = allocate_cidr(
                deps.base_network, deps.cidr_prefix, runs.active_cidrs() | leftover | taken
            )
            entry_subnets = carve_entry_subnets(cidr, entry_networks)
            try:
                runs.reserve_run(
                    run_id,
                    agent_id,
                    mission,
                    network_cidr=cidr,
                    entry_cidrs=",".join(entry_subnets.values()),
                    name=name,
                )
                return cidr, entry_subnets
            except IntegrityError:
                taken.add(cidr)  # another non-terminal run holds it (cross-process) — try next


def _agent_user_for(run_id: str) -> str:
    """The tailnet user the agent joins as — a stable convention derived from the run id, shared by
    run-create, the ACL provider, and teardown."""
    return f"run-{run_id[:8]}-agent"


def _resolve_run_name(name: str | None, agent_id: str, agent_name: str, mission_slug: str) -> str:
    """The run's label: the operator's, trimmed — or the lazy unique default when they leave
    it blank: `<mission> · <agent> #<n>`, where n is this agent's prior runs on this
    mission + 1."""
    chosen = (name or "").strip()
    if chosen:
        return chosen
    n = runs.count_runs_for(agent_id, mission_slug) + 1
    return f"{mission_slug} · {agent_name} #{n}"


def _acl_active_provider() -> list[RunNetwork]:
    """The DB-authoritative non-terminal run set for rendering the per-run ACL.

    Wired into the NetworkController so the ACL is rendered from persisted runs (shared across
    processes) rather than one process's in-memory view — no full `policy set` clobbers another's
    rules. Only agent_user + entry_cidrs matter for rendering, so the keys are left empty."""
    from xorcise.core.headscale import RunNetwork  # lazy: keep headscale off the boot path

    return [
        RunNetwork(agent_user=_agent_user_for(rid), auth_key="", router_key="", entry_cidrs=ec)
        for rid, ec in runs.active_run_networks()
    ]


def create_run(
    *,
    agent_name: str,
    mission_slug: str,
    budget_seconds: int | None,
    deps: RunCreateDeps,
    name: str | None = None,
    intel_policy: str | None = None,
) -> tuple[RunEntry, MissionPrompt]:
    """Create exactly one agent:one mission:one run and return it + its mission prompt.

    *intel_policy* is the per-run intel disclosure policy (runcontrol.intel_policy grammar); None ⇒
    the default "all" (every authored intel may be disclosed) — persisted on the run + used to size
    the prompt's disclosed-intel count."""
    policy = intel_policy if intel_policy is not None else "all"
    agent = agents.get(agent_name)
    if agent is None:
        raise NoAgentError(
            "register an agent first"
            if not agents.list_agents()
            else f"no agent named '{agent_name}' — register it first"
        )
    installed = get_installed(mission_slug, deps.install_root)
    if installed is None:
        # docker-run-style: auto-pull on demand. pull_mission raises
        # MissionNotInCatalogError / PullError if it cannot be obtained.
        #
        # `precheck` runs the nesting gate AFTER the cheap manifest fetch but BEFORE the multi-GB
        # image pull, and only for a LAB mission (static missions have no image and need no
        # nesting). Nesting is a HOST property, independent of the mission, so a host that cannot
        # nest is refused without first downloading an image it could never run.
        installed = pull_mission(mission_slug, deps.pull, precheck=deps.require_nested)

    run_id = uuid4().hex
    run_control_key = uuid4().hex  # per-run bearer for run-control (distinct from the tailnet key)
    # The run's label — the operator's, or the lazy `<mission> · <agent> #<n>` default. Resolved
    # once here so the static + lab paths persist the same name at reserve time.
    run_name = _resolve_run_name(name, agent.id, agent_name, mission_slug)
    # A static (attachment-only) mission has no runtime: short-circuit BEFORE any subnet
    # reservation, Headscale fence, container deploy, or target resolution.
    if installed.is_static:
        return _create_static_run(
            agent=agent,
            installed=installed,
            mission_slug=mission_slug,
            budget_seconds=budget_seconds,
            deps=deps,
            run_id=run_id,
            run_control_key=run_control_key,
            name=run_name,
            intel_policy=policy,
        )
    # Only a LAB mission reaches here, and a lab mission needs its stack nested inside the run
    # container. Checked HERE — after the static short-circuit, before the subnet reservation,
    # the Headscale fence and the deploy — so a host that cannot nest costs one error message
    # instead of a reserved CIDR, a minted auth key and a torn-down half-run. Static missions are
    # deliberately unaffected: they have no runtime at all. (For an auto-pulled mission this
    # already ran as the pull precheck, before the image download; re-running is cheap — the
    # verdict is memoised — and covers the already-installed path that skips the pull.)
    deps.require_nested()
    # And refuse an artifact fused on a base generation this XORCISE cannot run (e.g. a stale
    # engine-27 fuse after an upgrade) — it would die at deploy with no host-daemon fallback.
    deps.check_base_compat(installed.mission_ref.image)
    agent_user = _agent_user_for(run_id)
    assert installed.environment is not None  # is_lab ⇒ environment present (contract-enforced)
    entry_networks = tuple(installed.environment.entry_networks) or ("default",)
    # reserve the run's subnet as a DB row BEFORE deploy — durable + visible to every
    # process the instant it's taken (retires the in-memory _RESERVED_CIDRS). On any failure before
    # finalize, delete the reservation to free the subnet.
    cidr, entry_subnets = _reserve_run_subnet(
        deps, run_id, agent.id, mission_slug, entry_networks, name=run_name
    )
    try:
        return _create_run_with_cidr(
            agent=agent,
            installed=installed,
            mission_slug=mission_slug,
            budget_seconds=budget_seconds,
            deps=deps,
            run_id=run_id,
            run_control_key=run_control_key,
            agent_user=agent_user,
            cidr=cidr,
            entry_networks=entry_networks,
            entry_subnets=entry_subnets,
            intel_policy=policy,
        )
    except Exception:
        runs.delete_run(run_id)  # release the reservation on failure
        raise


def _create_run_with_cidr(
    *,
    agent: AgentEntry,
    installed: InstalledMission,
    mission_slug: str,
    budget_seconds: int | None,
    deps: RunCreateDeps,
    run_id: str,
    run_control_key: str,
    agent_user: str,
    cidr: str,
    entry_networks: tuple[str, ...],
    entry_subnets: dict[str, str],
    intel_policy: str = "all",
) -> tuple[RunEntry, MissionPrompt]:
    """Deploy the run on its already-reserved *cidr*/entry_subnets (the row was inserted
    by _reserve_run_subnet before this) and FINALIZE it with the fields known only after deploy."""
    from xorcise.core.runner.netoverride import target_ips_for

    # Only lab missions reach this path (create_run branches static → _create_static_run first).
    assert installed.environment is not None  # is_lab ⇒ environment present (contract-enforced)
    # entry_subnets were carved + persisted at reservation time; the ACL grants — and the router
    # advertises — these CIDRs (the compose network NAMES are the keys, the subnets the values).
    entry_cidrs = tuple(entry_subnets.values())

    net = deps.fence.create_run_network(run_id, agent_user, entry_cidrs)
    # capture the enforced per-run boundary config as observed facts (the anti-forgery
    # stream), independent of the agent's self-reported trace. Per-flow/violation facts
    # are future work.
    for fact in network_facts(run_id, agent_user=agent_user, entry_cidrs=entry_cidrs):
        deps.observed.record(fact)
    # Side-effecting: brings the mission + router up. Its RunnerEndpoints echo is no longer the
    # prompt's run-control URL (computes that from deps via _run_control_url), so the
    # return is intentionally unused.
    deps.control.deploy(
        DeployRequest(
            run_id=run_id,
            mission=MissionRef(
                mission_id=installed.mission_ref.mission_id,
                image=installed.mission_ref.image,
            ),
            network=NetworkSpec(
                tailnet=cidr,
                auth_key=net.router_key,  # the ROUTER joins with the tagged key (route approval)
                login_server=deps.login_server,
                entry_networks=entry_networks,
                routes=entry_cidrs,
                ca_cert=deps.ca_cert or None,  # air-gapped: router trusts the self-signed CA
                extra_hosts=deps.extra_hosts,
                static_ips=installed.environment.static_ips,  # pin to authored IPs
            ),
        ),
        credential=deps.api_key,
    )
    # the prompt's target address comes from the manifest static_ips (honored in the
    # deploy override above) resolved against the carved subnet — the only address source known at
    # create-time, since deploy does not wait for inner-container readiness (collect_targets has no
    # live IPs yet; it stays the seam for a future post-ready view). Port is not in the manifest;
    # the agent reads it from the objective. Surfacing name->IP is the gap we fill.
    targets = tuple(
        ConnectTarget(name=service, host=ip)
        for service, ip in target_ips_for(installed.environment.static_ips, entry_subnets).items()
    )
    # persist the run's target name->IP so the run-control /mission brief can resolve the
    # same <name-target-ip-> placeholder the prompt does (the manifest objective has no runtime IP).
    from xorcise.core.contracts.telemetry import ObservedFact

    for t in targets:
        deps.observed.record(ObservedFact(run_id=run_id, kind="target", name=t.name, value=t.host))
    # GUI/launch plane: pick the harness's launch provider ONCE — its effective launch mode
    # (host vs container) drives BOTH the reachable run-control host baked into the persisted
    # mission AND the bounded per-harness preamble. Lazy import keeps concrete providers off
    # role:control's boot path (plane isolation), mirroring launch_profile_for.
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.base import LaunchContext
    from xorcise.core.runs.launch.registry import select as select_launch

    source_agent = agent.kind or "generic"
    load_launch_providers()
    launch_provider, _ = select_launch(source_agent)
    # Only a HOST-ONLY harness (Claude Code, `claude -p`; it cannot use a container endpoint) gets
    # the server's loopback (deps.host) baked into run-control. Container/dual-mode/generic
    # harnesses keep the agent-facing advertise_host (host.docker.internal locally) — the
    # historical default.
    effective_launch_mode = agent.launch_mode or (
        "host" if "container" not in launch_provider.launch_modes else "container"
    )
    run_control_host = (
        deps.host if effective_launch_mode == "host" and deps.host else deps.advertise_host
    )
    mission = assemble_mission_prompt(
        run_id=run_id,
        mission=mission_slug,
        objective=installed.manifest.metadata.objective,
        login_server=deps.login_server,
        join_key=net.auth_key,
        # the agent reaches run-control on the server REST API (advertise_host:rest_port/api),
        # NOT the Headscale control plane that endpoints.control_url echoes.
        run_control_url=_run_control_url(deps, run_id, host=run_control_host),
        run_control_key=run_control_key,
        targets=targets,
        # air-gapped self-sufficiency: hand the agent the CA + host alias the router
        # already gets, so the rendered prompt is everything it needs to join.
        ca_cert=deps.ca_cert,
        host_alias=deps.extra_hosts[0] if deps.extra_hosts else "",
        # enumerate the manifest's expected artifacts so a prompt-only agent knows what
        # to submit (the flag is the artifact named "flag").
        artifacts=tuple(
            ConnectArtifact(name=a.name, required=a.required, description=a.description)
            for a in installed.manifest.artifacts
        ),
        # surface the manifest's companion files so a prompt-only agent learns they
        # exist and can mint the signed download link by name (GET /attachments/<name>).
        attachments=tuple(
            ConnectAttachment(name=a.name, media_type=a.media_type)
            for a in installed.manifest.attachments
        ),
        # Size the prompt's disclosed-intel count off the SAME policy filter the run-control gate
        # applies, so the advert never promises intel this run may not disclose.
        intel_available=len(allowed_intel(intel_policy, installed.manifest.intel)),
    )
    budget = budget_seconds if budget_seconds is not None else deps.default_budget
    # A known harness contributes a bounded preamble to the agent-facing mission, baked into the
    # prompt ONCE here (the rendered prompt is persisted verbatim below). Reuses the launch provider
    # + effective mode already selected above for the run-control host.
    mission_preamble = (
        agent.mission_preamble
        if agent.mission_preamble is not None
        else launch_provider.mission_preamble(
            LaunchContext(
                run_id=run_id,
                source_agent=source_agent,
                launch_mode=effective_launch_mode,
            )
        )
    )
    # the row was already inserted (network_cidr + entry_cidrs) by _reserve_run_subnet;
    # fill in the fields known only now that deploy succeeded.
    run = runs.finalize_run(
        run_id,
        budget_seconds=budget,
        prompt=render_prompt_text(mission, preamble=mission_preamble),
        run_control_key=run_control_key,
        join_key=net.auth_key,  # persist so GET /connect can serve it
        model=agent.model,
        sandbox_ref=installed.mission_ref.image,
        agent_version=agent.version,
        source_agent=source_agent,
        mission_version=installed.install_revision,
        intel_policy=intel_policy,
    )
    # the ACL was applied in create_run_network BEFORE this row persisted, so a truly
    # concurrent create in another process could have rendered an incomplete (clobbered) policy.
    # Now that the row is persisted, re-render from the DB-authoritative set so every non-terminal
    # run's rule is present — the last concurrent create to persist restores the complete policy.
    deps.fence.reconcile_acl()
    return run, mission


def _create_static_run(
    *,
    agent: AgentEntry,
    installed: InstalledMission,
    mission_slug: str,
    budget_seconds: int | None,
    deps: RunCreateDeps,
    run_id: str,
    run_control_key: str,
    name: str | None = None,
    intel_policy: str = "all",
) -> tuple[RunEntry, MissionPrompt]:
    """Create an attachment-only static run (static-mission-support).

    No subnet, no Headscale fence, no container deploy, no target resolution — a static mission
    has no runtime. The run row is reserved with the default empty network_cidr (excluded from the
    non-terminal-run unique index, so concurrent static runs never collide) and finalized with a
    mission prompt that omits the tailnet-join + Targets sections (render_prompt_text gates those on
    join_key/targets). Attachment delivery, artifact submission, checks/grading, and termination all
    read the installed manifest on disk and need no deployed environment."""
    if warning := installed.manifest.static_environment_warning:
        # Migration-compat: a lingering environment block is ignored, never deployed — just warn.
        log.warning("static run %s: %s", run_id, warning)
    # Empty network_cidr ⇒ holds no subnet (excluded from the non-terminal-run unique index).
    runs.reserve_run(
        run_id,
        agent.id,
        mission_slug,
        network_cidr="",
        entry_cidrs="",
        name=name,
        intel_policy=intel_policy,
    )
    try:
        # Same launch-provider selection as the lab path: the effective launch mode drives the
        # reachable run-control host baked into the mission and the bounded per-harness preamble.
        from xorcise.core.harness_adapters import load_launch_providers
        from xorcise.core.runs.launch.base import LaunchContext
        from xorcise.core.runs.launch.registry import select as select_launch

        source_agent = agent.kind or "generic"
        load_launch_providers()
        launch_provider, _ = select_launch(source_agent)
        effective_launch_mode = agent.launch_mode or (
            "host" if "container" not in launch_provider.launch_modes else "container"
        )
        run_control_host = (
            deps.host if effective_launch_mode == "host" and deps.host else deps.advertise_host
        )
        mission = assemble_mission_prompt(
            run_id=run_id,
            mission=mission_slug,
            objective=installed.manifest.metadata.objective,
            login_server="",  # no tailnet
            join_key="",  # no tailnet key → prompt omits the join recipe
            run_control_url=_run_control_url(deps, run_id, host=run_control_host),
            run_control_key=run_control_key,
            targets=(),  # no targets → prompt omits the Targets section
            ca_cert="",
            host_alias="",
            artifacts=tuple(
                ConnectArtifact(name=a.name, required=a.required, description=a.description)
                for a in installed.manifest.artifacts
            ),
            attachments=tuple(
                ConnectAttachment(name=a.name, media_type=a.media_type)
                for a in installed.manifest.attachments
            ),
            intel_available=len(allowed_intel(intel_policy, installed.manifest.intel)),
        )
        budget = budget_seconds if budget_seconds is not None else deps.default_budget
        mission_preamble = (
            agent.mission_preamble
            if agent.mission_preamble is not None
            else launch_provider.mission_preamble(
                LaunchContext(
                    run_id=run_id,
                    source_agent=source_agent,
                    launch_mode=effective_launch_mode,
                )
            )
        )
        run = runs.finalize_run(
            run_id,
            budget_seconds=budget,
            prompt=render_prompt_text(mission, preamble=mission_preamble),
            run_control_key=run_control_key,
            join_key="",  # no tailnet key for a static run
            model=agent.model,
            sandbox_ref=installed.mission_ref.image,  # "" for static (no fused image)
            agent_version=agent.version,
            source_agent=source_agent,
            mission_version=installed.install_revision,
            intel_policy=intel_policy,
        )
        return run, mission
    except Exception:
        runs.delete_run(run_id)  # release the reserved row on failure
        raise
