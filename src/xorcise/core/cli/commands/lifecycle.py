"""Lifecycle commands — the local lifecycle driver (cli).

`serve` runs the three ASGI apps concurrently; `up` runs `serve` detached and
prints the UI URL; `status`/`doctor` probe health; `down` stops the process.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import httpx
import typer

from xorcise import __version__
from xorcise.core.cli._diagnostics import (
    Check,
    control_plane,
    disk_space,
    docker_compose_v2,
    docker_daemon,
    docker_present,
    external_control_plane,
    home_present,
    nested_containers,
    openssl_present,
    probe_channel,
    python_version,
    tun_device,
)
from xorcise.core.cli._frontend import ensure_frontend_ready
from xorcise.core.cli._preflight import (
    PortScanError,
    conflict_message,
    ports_in_use,
    resolve_ports,
)
from xorcise.core.cli._shared import (
    EXIT_CODES_EPILOG,
    app,
    console,
    emit_json,
    err_console,
    golden_path_steps,
)
from xorcise.core.cli._update import begin_update_check
from xorcise.core.cli._ux import print_table, step_progress, ux_table
from xorcise.core.cli._ux import service_label as _service_label
from xorcise.core.config import get_settings, ui_url
from xorcise.core.headscale import provision
from xorcise.core.home import (
    clear_transient,
    init_home,
    pid_file,
    purge_home,
    read_runtime_ports,
    runtime_ports_file,
    scaffold_config,
    write_runtime_ports,
    xorcise_home,
)
from xorcise.core.roles.boot import AppSpec


def _bind_hosts(host: str | tuple[str, ...]) -> list[str]:
    """Hosts to actually listen on for a given spec host.

    A tuple lists every address the app must serve (role_all's narrowed
    agent-facing set — loopback + docker gateway); each element becomes one
    listener. An IPv4-wildcard ("0.0.0.0") listener doesn't answer the IPv6
    loopback that the loopback hostname resolves to first on many systems, so
    the GUI fails to load under that hostname (works only via the IPv4
    address). Pair it with an IPv6-loopback ("::1") companion so both reach
    the apps. "::1" is loopback-only, so it never collides with the IPv4
    wildcard under any IPV6_V6ONLY setting.
    """
    hosts = (host,) if isinstance(host, str) else host
    out: list[str] = []
    for h in hosts:
        for bind in [h, "::1"] if h == "0.0.0.0" else [h]:
            if bind not in out:
                out.append(bind)
    return out


def _serve_banner(role: str, specs: list[AppSpec]) -> str:
    """Banner for `serve` — only roles that boot REST advertise the UI URL."""
    if any(s.port == get_settings().rest_port for s in specs):
        return f"serving role:{role} — UI at {ui_url()}"
    ports = ", ".join(f":{s.port}" for s in specs)
    return f"serving role:{role} on {ports} — no REST/UI plane for this role"


def next_steps_block(ui: str) -> str:
    """Ready banner + the golden path to a graded run, for `up` to print.

    Rendered from the SAME canonical list as the root epilog (minus 'Start
    XORCISE', which just happened) — the two screens can never disagree."""
    steps = "\n".join(golden_path_steps(skip_first=True))
    return f"xorcise up — UI at {ui}\nNext steps:\n{steps}"


def _headscale_plan(settings: object, *, stub: bool, docker_ok: bool, ours: bool = False) -> str:
    """Decide what `up` does about Headscale (pure; unit-tested).

    Returns one of: "skip-stub" (Docker-less demo), "fail-docker" (real-by-default needs
    Docker), "external" (a configured headscale_url → use it), "local" (zero-config default
    → provision a local control plane).

    `ours` says the configured headscale_url is the value OUR OWN managed block wrote (see
    provision.managed_url). It exists because a successful local `up` records its own URL in
    config.toml, and once merged into Settings that is indistinguishable from an
    operator-chosen remote. Both confusions bite: reading ours as "external" made the next
    `up` skip provisioning, so no container existed and every run creation 503'd while `up`
    reported success; reading an operator's remote as ours would silently ignore an explicit
    `config set-network --headscale-url`. Comparing the URL is exact — an ownership marker
    alone is not, because it survives an `up` that follows a new remote being configured."""
    if stub:
        return "skip-stub"
    if not docker_ok:
        return "fail-docker"
    if getattr(settings, "headscale_url", "") and not ours:
        return "external"
    return "local"


def _require_prerequisites(*, stub: bool) -> None:
    """Fail fast, with the fix, when the host cannot run XORCISE.

    Runs BEFORE anything is written, so a missing prerequisite costs the user
    nothing and leaves no half-provisioned home behind. `--stub` is the
    Docker-less demo path, so it only needs the data directory."""
    checks = [home_present()] if stub else _environment_checks()
    blockers = [c for c in checks if not c.ok and c.level == "blocker"]
    # A missing data directory is not a blocker for `up` — `up` is what creates it.
    blockers = [c for c in blockers if not (c.name == "data directory" and "missing" in c.detail)]
    if not blockers:
        return
    for c in blockers:
        # Prefer doctor's friendly subject ("Docker Compose v2 plugin is missing")
        # over the bare detail ("missing"), so the line reads as a sentence.
        subject = _DOCTOR_FRIENDLY.get((c.name, False)) or f"{c.name}: {c.detail}"
        err_console.print(f"[err]error[/err]: {subject} — {c.remediation}", highlight=False)
    if not stub:
        err_console.print(
            "\n[dim]run a Docker-less demo instead: xorcise up --stub[/dim]\n"
            "[dim]full diagnosis: xorcise doctor[/dim]"
        )
    raise typer.Exit(1)


def _headscale_workdir() -> Path:
    return Path(xorcise_home()) / "headscale"


def _config_path() -> Path:
    return Path(xorcise_home()) / "config.toml"


def _maybe_provision_headscale(
    settings: object, *, stub: bool, docker_ok: bool, deployment_topology: str = "local"
) -> str:
    """Act on the Headscale plan; returns the plan. Caller handles 'fail-docker' exit."""
    configured = getattr(settings, "headscale_url", "")
    plan = _headscale_plan(
        settings,
        stub=stub,
        docker_ok=docker_ok,
        ours=bool(configured) and configured == provision.managed_url(_config_path()),
    )
    if plan == "local":
        # Idempotent, so re-running it is how a control plane whose containers went away
        # (reboot, `docker prune`) is repaired — silently skipping that was the bug.
        res = provision.ensure_up(
            _headscale_workdir(), _config_path(), deployment_topology=deployment_topology
        )
        get_settings.cache_clear()  # the managed block is now live
        console.print(f"local Headscale ready at {res.url}")
    elif plan == "external":
        url = configured
        # VERIFY, never announce from config alone: a saved preference is not a
        # connection. Failing here costs one `up`; not failing here cost every run
        # afterwards, each one blaming a control plane the operator thought was fine.
        check = external_control_plane(url)
        if not check.ok:
            err_console.print(
                f"[err]error[/err]: the configured control plane at {url} is not reachable",
                highlight=False,
            )
            err_console.print(f"{check.remediation}")
            raise typer.Exit(1)
        console.print(f"using external control plane at {url}")
        # Reaching the URL is NOT the whole dependency: control operations shell into a LOCAL
        # container (rest/run_create.py::_real_headscale_cli docker-execs
        # settings.headscale_container), so on a host that serves run creation an external URL
        # alone can still leave every run 503-ing. Warn rather than exit — whether a genuinely
        # distributed topology keeps a local container here is not something this command can
        # establish, and refusing to start would break any setup that does work. The point is
        # that `up` must not imply run creation is ready when it cannot confirm that.
        serves_runs = not getattr(settings, "use_stubs", False) and getattr(
            settings, "role", "all"
        ) in {"all", "runner"}
        if serves_runs:
            local = control_plane(getattr(settings, "headscale_container", "headscale"))
            if not local.ok:
                err_console.print(
                    f"[warn]warning[/warn]: no local control-plane container "
                    f"({local.detail}) — this host runs the runner, which drives Headscale "
                    f"through that container, so run creation will fail until it exists",
                    highlight=False,
                )
                err_console.print("[dim]check: xorcise doctor[/dim]")
    elif plan == "skip-stub":
        console.print("stub mode — skipping Headscale")
    return plan


def _maybe_teardown_headscale() -> None:
    wd = _headscale_workdir()
    if not provision.is_owned(wd):
        return
    # A stopped Docker daemon means there is nothing to tear down (no daemon ⇒ no containers), yet
    # `provision.teardown` → `docker compose down -v` would raise ProvisionError and abort the whole
    # `xorcise down`. Skip gracefully and KEEP the ownership marker so a later `xorcise down`
    # completes the teardown (and removes the volumes) once Docker is back up.
    if not docker_daemon().ok:
        console.print(
            "Docker daemon not running — skipping local Headscale teardown; "
            "run 'xorcise down' again once Docker is up to remove its volumes"
        )
        return
    with step_progress("removing local Headscale"):
        removed = provision.teardown(wd, _config_path())
    if removed:
        console.print("removed local Headscale")


# Planes each role binds (mirrors roles/boot role_<x>.apps()); port resolution must run
# BEFORE activate(role) because apps() captures get_settings() ports at build time.
_ROLE_PLANES: dict[str, tuple[str, ...]] = {
    "all": ("rest", "otlp"),
    "control": ("rest",),
    "collector": ("otlp",),
    "runner": ("runner",),
    "headscale": ("headscale",),
}
_PLANE_ENV = {
    "rest": "XORCISE_REST_PORT",
    "otlp": "XORCISE_OTLP_PORT",
    "runner": "XORCISE_RUNNER_PORT",
    "headscale": "XORCISE_HEADSCALE_PORT",
}


def _apply_runtime_env(role: str, *, stub: bool, ports: dict[str, int] | None = None) -> None:
    """Align the process env so get_settings() reflects the booted role (+ optional stubs).

    The deps-builders pick real-vs-stub adapters from settings.role +
    settings.use_stubs; serve/up own the booted role, so they stamp it into the env here. `ports`
    stamps flag/auto-increment-resolved ports — only values that differ from the current
    settings, so an unchanged resolution leaves the env untouched. The serve subprocess
    spawned by `up` inherits os.environ, so it sees the same resolved ports."""
    os.environ["XORCISE_ROLE"] = role
    if stub:
        os.environ["XORCISE_USE_STUBS"] = "1"
    if ports:
        s = get_settings()
        for plane, value in ports.items():
            if getattr(s, f"{plane}_port") != value:
                os.environ[_PLANE_ENV[plane]] = str(value)
    get_settings.cache_clear()


def _resolve_role_ports(role: str, overrides: dict[str, int | None]) -> dict[str, int]:
    """Auto-increment each of the role's planes to a free port, with a notice per move.

    CLI flag values in `overrides` (None/absent = not given) take precedence over settings.
    Exits with a clear error when a plane's scan window has no free port."""
    s = get_settings()
    wanted: dict[str, int] = {}
    for plane in _ROLE_PLANES.get(role, ()):
        override = overrides.get(plane)
        # isinstance guards direct (non-CLI) calls, where typer defaults are OptionInfo.
        wanted[plane] = override if isinstance(override, int) else getattr(s, f"{plane}_port")
    try:
        resolved = resolve_ports(s.host, wanted)
    except PortScanError as exc:
        err_console.print(f"[err]{exc}[/err]")
        raise typer.Exit(1) from None
    for plane, requested in wanted.items():
        if resolved[plane] != requested:
            console.print(
                f"[warn]port {requested} ({plane}) is busy — using {resolved[plane]} instead[/warn]"
            )
    return resolved


def _ensure_db_ready() -> None:
    """Boot-time DB handling. A database is `fresh` (scaffold to head), `ready` (boot), or
    `stale` (refuse loudly and defer to `xorcise db upgrade`) — populated data is never
    migrated on boot.

    Scaffolds a *fresh* (schema-less) DB to head — bootstrap, consistent with
    home creation. Refuses to boot a DB that has data but is behind head:
    migrating populated data stays an explicit ``xorcise db upgrade``. Runs in
    both ``up`` and ``serve`` (idempotent — the spawned serve sees 'current'),
    so a bare foreground serve never boots 'healthy' against an empty DB.
    Caches are cleared first so it reflects the current XORCISE_HOME.
    """
    from xorcise.core import config, db

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    state = db.boot_state()
    if state == "fresh":
        db.upgrade()
        console.print("prepared the database")
    elif state == "stale":
        err_console.print(
            "[err]database is behind the schema — run 'xorcise db upgrade' first[/err]"
        )
        raise typer.Exit(1)


@app.command(rich_help_panel="Getting started")
def up(
    stub: bool = typer.Option(False, "--stub", help="Force stub adapters (Docker-less dev/demo)."),
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        min=1,
        max=65535,
        help="REST port (default from config; auto-increments when busy).",
    ),
    otlp_port: int | None = typer.Option(
        None,
        "--otlp-port",
        min=1,
        max=65535,
        help="OTLP port (default from config; auto-increments when busy).",
    ),
) -> None:
    """Start XORCISE in the background and print the interface URL.

    Already-running is success, not an error — safe to re-run any time.
    """
    pf = pid_file()
    if pf.exists():
        try:
            os.kill(int(pf.read_text()), 0)
        except (ProcessLookupError, ValueError):
            pf.unlink(missing_ok=True)
        except PermissionError:
            # The pid exists but belongs to another user — treat as running.
            console.print(f"already running — UI at {_effective_ui_url()}")
            return
        else:
            # Idempotent converge: 'already true' is exit 0, matching `down` on
            # not-running — scripts and agents re-run `up` defensively.
            console.print(f"already running — UI at {_effective_ui_url()}")
            return

    # Prerequisites BEFORE anything is written. A missing Compose v2 plugin used to
    # surface ~20 lines in, after the home was created, the DB migrated and two RSA
    # keys generated — as a raw `docker compose … failed` with no remediation.
    _require_prerequisites(stub=stub)

    init_home()
    scaffold_config(xorcise_home())
    # Kicked off here so the network hit overlaps the slow boot steps below (db,
    # Headscale, frontend build, health poll); the closure is read at the ready
    # banner. No-TTY and opted-out invocations get a constant-None closure.
    check_update = begin_update_check()
    _ensure_db_ready()  # clears settings/engine caches + scaffolds fresh / refuses stale DB
    settings = get_settings()  # post-scaffold, post-cache-clear → honors config.toml host/ports

    # Resolve each plane to a free port (auto-increment past busy ones) and stamp the choices
    # into the env, so this process AND the spawned serve (which inherits os.environ) agree.
    resolved = _resolve_role_ports("all", {"rest": port, "otlp": otlp_port})
    _apply_runtime_env(settings.role, stub=stub, ports=resolved)
    settings = get_settings()

    # Headscale: provision a local one by default. External (headscale_url set) and
    # --stub skip it. Prerequisites were already gated above (_require_prerequisites), so by
    # here Docker + Compose v2 + openssl are known good. Runs before serve so the server reads
    # the managed config block on boot.
    _maybe_provision_headscale(
        settings, stub=stub, docker_ok=True, deployment_topology=settings.deployment_topology
    )
    settings = get_settings()  # refresh: the managed block may have just been written

    # Ensure the /ui static export is built & consistent before serving (source checkout only;
    # installed wheels bake it in). Runs after the fast pre-flight so cheap checks fail first, and
    # in the foreground `up` process so its output reaches the user (`serve` is DEVNULL-piped).
    ensure_frontend_ready(console)

    serve_cmd = [sys.executable, "-m", "xorcise", "serve"]
    if stub:
        serve_cmd.append("--stub")
    serve_log = Path(xorcise_home()) / "serve.log"
    with serve_log.open("wb") as log_file:
        # start_new_session: the detached server must survive the operator's Ctrl-C
        # during the health poll below (same process group would take the SIGINT).
        # stderr goes to serve.log so a failed boot is diagnosable.
        proc = subprocess.Popen(
            serve_cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            start_new_session=True,
        )
    pf.write_text(str(proc.pid))
    write_runtime_ports(resolved)  # sibling CLIs (ui/status/RestClient) discover relocations here
    health = f"http://{settings.host}:{settings.rest_port}/api/health"
    healthy = False
    try:
        for _ in range(50):
            try:
                if httpx.get(health, timeout=1).status_code == 200:
                    healthy = True
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
    except KeyboardInterrupt:
        err_console.print(
            "interrupted — XORCISE may still be starting; "
            "check [value]xorcise status[/value] or run [value]xorcise down[/value]"
        )
        raise typer.Exit(130) from None
    if not healthy:
        err_console.print(
            "[err]error[/err]: XORCISE did not become healthy in 10s — "
            f"see {serve_log} or run [value]xorcise serve[/value] in the foreground; "
            "[value]xorcise doctor[/value] checks prerequisites"
        )
        raise typer.Exit(1)
    # Ready. The banner/notice happen OUTSIDE the interrupt guard above: a Ctrl-C
    # landing in the update-check join (≤0.75s) must not relabel a successful start
    # as 'may still be starting' — the server IS up and the pid file is written.
    if console.is_terminal:
        console.print(
            f"[accent]⊕ X O R C I S E[/] [dim]v{__version__} — Trust Evidence, not Claims.[/dim]"
        )
        notice = check_update()
        if notice:
            console.print(notice)
    console.print(next_steps_block(ui_url()))


def _runtime_port_overlay() -> dict[str, int]:
    """The running server's actual ports (auto-increment may have moved them off config).

    Only trusted while the pid file exists — a stale record must not redirect sibling
    CLI processes away from the configured ports. A plane whose XORCISE_*_PORT env var
    is set on THIS invocation is excluded: that is a deliberate 'talk to THIS port'
    instruction and outranks discovery (same precedence as the REST client), so
    status/doctor and the data commands always agree on the endpoint."""
    if not pid_file().exists():
        return {}
    runtime = read_runtime_ports() or {}
    return {
        plane: port
        for plane, port in runtime.items()
        if _PLANE_ENV.get(plane, "") not in os.environ
    }


def _effective_ui_url() -> str:
    """ui_url with the runtime-resolved REST port overlaid (for sibling processes)."""
    s = get_settings()
    return f"http://{s.host}:{_runtime_port_overlay().get('rest', s.rest_port)}/ui"


def _running_server_ports() -> set[int]:
    """The live server's rest/otlp ports — empty when no server is running.

    Lets `doctor` tell 'port held by OUR running server' (healthy) apart from a
    foreign process squatting on a plane port (a real conflict)."""
    pf = pid_file()
    if not pf.exists():
        return set()
    try:
        os.kill(int(pf.read_text()), 0)
    except (ProcessLookupError, ValueError):
        return set()
    except PermissionError:
        pass  # alive, just not ours to signal — still the server
    s = get_settings()
    runtime = read_runtime_ports() or {}
    return {runtime.get(plane, getattr(s, f"{plane}_port")) for plane in ("rest", "otlp")}


@app.command(rich_help_panel="Getting started")
def ui() -> None:
    """Print the local interface URL (warns when XORCISE is not running)."""
    s = get_settings()
    # URL on stdout always (scripts read it); the liveness warning goes to stderr.
    console.print(_effective_ui_url())
    rest = _runtime_port_overlay().get("rest", s.rest_port)
    if probe_channel("rest", f"http://{s.host}:{rest}/api/health").ok:
        # A URL that answers but from a different home is another instance — say so,
        # so the printed link isn't silently this home's neighbour's.
        _note_foreign_instance(s.host, rest)
    else:
        err_console.print(
            "[warn]XORCISE is not running — start it with [value]xorcise up[/value][/warn]"
        )


# Probe names render via the shared human-facing service vocabulary (_ux.service_label).


def _foreign_instance_home(host: str, rest_port: int) -> str | None:
    """The XORCISE_HOME of the instance answering on `rest_port`, when it is NOT
    this home's — else None. Lets doctor say 'held by another XORCISE instance'
    instead of accusing an unknown process."""
    try:
        info = httpx.get(f"http://{host}:{rest_port}/api/system", timeout=1).json()
    except (httpx.HTTPError, ValueError):
        return None
    their_home = str(info.get("home") or "") if isinstance(info, dict) else ""
    if not their_home:
        return None
    with contextlib.suppress(OSError):
        if Path(their_home).resolve() == Path(xorcise_home()).resolve():
            return None
    return their_home


def _note_foreign_instance(host: str, rest_port: int) -> None:
    """After `down` (no pid file in THIS home), a healthy probe may be answered by
    ANOTHER instance on the same ports (shared machine, default ports) — name whose
    it is, so 'All services are healthy.' never reads as ours when it is not."""
    if pid_file().exists():
        return
    with contextlib.suppress(Exception):
        info = httpx.get(f"http://{host}:{rest_port}/api/system", timeout=1).json()
        their_home = str(info.get("home") or "")
        if their_home and Path(their_home).resolve() != Path(xorcise_home()).resolve():
            console.print(
                "[dim]note: no instance was started from this home — the responding "
                f"services belong to the instance at {their_home}[/dim]"
            )


def _state_word(check: Check) -> str:
    """Probe detail → the user-facing state vocabulary."""
    if check.ok:
        return "[ok]Healthy[/ok]"
    return {
        "down": "[err]Stopped[/err]",
        "missing": "[err]Missing[/err]",
        "unreachable": "[err]Unreachable[/err]",
    }.get(check.detail, f"[err]{check.detail.title()}[/err]")


@app.command(rich_help_panel="Getting started", epilog=EXIT_CODES_EPILOG)
def status(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the checks + resolved ports as JSON (for scripting)."
    ),
) -> None:
    """Check whether the local XORCISE services are running (non-zero if anything is down).

    See also: system, which asks the running instance what it sees from the inside."""
    s = get_settings()
    runtime = _runtime_port_overlay()
    rest = runtime.get("rest", s.rest_port)
    otlp = runtime.get("otlp", s.otlp_port)
    checks = [
        probe_channel("rest", f"http://{s.host}:{rest}/api/health"),
        probe_channel("otlp", f"http://{s.host}:{otlp}/healthz"),
        docker_daemon(),
    ]
    ok = all(c.ok for c in checks)
    # `is True` guards direct (non-CLI) calls where the typer default is an OptionInfo.
    if as_json is True:
        emit_json(
            {
                "ok": ok,
                "ports": {"rest": rest, "otlp": otlp},
                "checks": [asdict(c) for c in checks],
            }
        )
    else:
        addresses = {
            "rest": f"http://{s.host}:{rest}",
            "otlp": f"http://{s.host}:{otlp}",
            "docker": "local daemon",
        }
        rest_check = checks[0]
        table = ux_table("Service", "State", "Address", title="XORCISE status")
        # The interface rides on the REST plane — surfaced first because it is
        # the row a person actually visits.
        table.add_row("Interface", _state_word(rest_check), f"http://{s.host}:{rest}/ui")
        for c in checks:
            table.add_row(_service_label(c.name), _state_word(c), addresses[c.name])
        print_table(table)
        if ok:
            console.print("[ok]All services are healthy.[/ok]")
            _note_foreign_instance(s.host, rest)
        else:
            down = ", ".join(_service_label(c.name) for c in checks if not c.ok)
            console.print(
                f"[err]{down}: not responding[/err] — start XORCISE with "
                "[value]xorcise up[/value]; [value]xorcise doctor[/value] checks prerequisites"
            )
    if not ok:
        raise typer.Exit(1)


# Doctor check names → friendly one-liners for the human view (the --json shape
# keeps the raw names/details — scripts depend on them).
_DOCTOR_FRIENDLY = {
    ("docker", True): "Docker installed",
    ("docker", False): "Docker is not installed",
    ("docker daemon", True): "Docker daemon reachable",
    ("docker compose", True): "Docker Compose v2 plugin present",
    ("docker compose", False): "Docker Compose v2 plugin is missing",
    ("openssl", True): "openssl present",
    ("openssl", False): "openssl is missing",
    # Named for what it DOES (every run joins this network) with the component in
    # parentheses, so the line is meaningful without knowing the architecture.
    ("control plane", True): "run network ready (Headscale)",
    ("control plane", False): "run network is not reachable (Headscale)",
}


def _doctor_line(c: Check) -> str:
    friendly = _DOCTOR_FRIENDLY.get((c.name, c.ok))
    if c.name == "docker daemon" and not c.ok:
        # 'permission denied' vs 'unreachable' are different problems with
        # different fixes — the detail says which, so don't flatten it.
        friendly = f"Docker daemon: {c.detail}"
    if c.name == "data directory":  # the detail carries the actual (env-resolved) path
        friendly = f"data directory {'ready' if c.ok else 'missing'} ({c.detail})"
        if not c.ok and "not writable" in c.detail:
            friendly = f"data directory is not writable ({c.detail.split(' is not')[0]})"
    if friendly is None:  # ports/python/disk carry their own already-specific wording
        friendly = f"{c.name}: {c.detail}"
    if c.ok:
        return f"  [ok]✓[/ok] {friendly}"
    mark = "[warn]![/warn]" if c.level == "warning" else "[err]✗[/err]"
    return f"  {mark} {friendly} — {c.remediation}"


def _environment_checks() -> list[Check]:
    """Everything a real run needs from the host, in report order.

    Docker-dependent probes are gated on the binary being present, so a host with
    no Docker gets ONE actionable line instead of five. Warnings are reported but
    never fail the verdict."""
    checks = [python_version(), docker_present()]
    if checks[1].ok:
        # Distinct label: 'docker' is the binary on PATH, 'docker daemon' is it answering.
        checks.append(replace(docker_daemon(), name="docker daemon"))
        # `docker compose` (v2 plugin) — every non-stub `up` provisions Headscale with it.
        checks.append(docker_compose_v2())
        checks.append(disk_space())
    checks.append(openssl_present())
    checks.append(tun_device())
    checks.append(home_present())
    return checks


@app.command(rich_help_panel="Getting started")
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Emit the checks as JSON (for scripting)."),
) -> None:
    """Diagnose setup problems and say exactly how to fix them (non-zero on failure)."""
    try:
        s = get_settings()
    except OSError as exc:
        # Diagnose the unwritable data directory instead of dying on it — this is
        # exactly the condition doctor exists to explain.
        err_console.print(
            f"[err]✗[/err] data directory is not usable — {exc}\n"
            "fix the permissions on your XORCISE_HOME (or unset it), then re-run "
            "[value]xorcise doctor[/value]"
        )
        raise typer.Exit(1) from exc
    env_checks = _environment_checks()
    # Only under `doctor` — it starts a privileged container, so it must not join the check list
    # `up` runs (and `up` must work on a host that cannot nest: static missions and the UI need
    # nothing nested). Gated on the docker daemon being up, so a Docker-less host gets one
    # actionable line rather than two; and skipped in stub mode, which deploys nothing and so has
    # no nesting precondition to check (same reasoning as the control-plane probe below).
    if not s.use_stubs and all(c.ok for c in env_checks if c.name in {"docker", "docker daemon"}):
        env_checks.append(nested_containers())
    port_checks: list[Check] = []
    server_ports = _running_server_ports()
    # Probe the ports the server actually runs on (runtime overlay, same resolution
    # as `status`) — not the configured defaults a relocated server moved off.
    runtime = _runtime_port_overlay()
    effective = {
        plane: runtime.get(plane, getattr(s, f"{plane}_port")) for plane in ("rest", "otlp")
    }
    plane_names = {port: plane for plane, port in effective.items()}
    taken = set(ports_in_use(s.host, list(effective.values())))
    # If the REST plane is answered by another XORCISE instance, its sibling plane
    # is that instance's too — attribute both rather than accusing "another
    # process" of holding otlp.
    foreign_home = (
        _foreign_instance_home(s.host, effective["rest"])
        if effective["rest"] in taken and effective["rest"] not in server_ports
        else None
    )
    # "Should XORCISE be up right now?" — true when this home started it, or when
    # one of its planes is demonstrably ours. Only then is a SILENT plane a fault
    # rather than the ordinary not-running state.
    expected_up = bool(server_ports & taken) or pid_file().exists()
    for plane, port in effective.items():
        if port not in taken:
            # A plane nobody is listening on: invisible before (doctor only walked
            # the BOUND ports), so a half-down instance reported 'No problems found'.
            if expected_up:
                port_checks.append(
                    Check(
                        f"port {port} ({plane})",
                        False,
                        "not listening",
                        "XORCISE is running but this service is not bound — restart it: "
                        "xorcise down && xorcise up",
                    )
                )
            continue
        if port in server_ports:
            # The holder is OUR running service — that's health, not a conflict.
            port_checks.append(
                Check(
                    f"port {port} ({plane})",
                    True,
                    "in use by the running XORCISE service",
                )
            )
        else:
            # Name the holder when it is ANOTHER XORCISE instance — "stop the
            # process using it" reads as a fault when it is simply not ours.
            their_home = foreign_home
            if their_home:
                port_checks.append(
                    Check(
                        f"port {port} ({plane})",
                        True,
                        f"in use by the XORCISE instance at {their_home}",
                    )
                )
            else:
                port_checks.append(
                    Check(
                        f"port {port}",
                        False,
                        "in use by another process",
                        conflict_message(s.host, [port], planes=plane_names),
                    )
                )
    # The control plane is a SERVICE, not a host prerequisite: it only exists once
    # `up` has provisioned it, so it is probed under exactly the condition that makes
    # a silent plane a fault (`expected_up`) — otherwise a healthy, not-yet-started
    # host would fail its own diagnosis. Stub mode has no control plane by design.
    if expected_up and not s.use_stubs:
        port_checks.append(control_plane(s.headscale_container))
    checks = [*env_checks, *port_checks]

    # Warnings are reported but never fail the verdict — advisory prerequisites
    # (disk, /dev/net/tun, python) must not flip a healthy host to exit 1.
    ok = all(c.ok for c in checks if c.level == "blocker")
    if as_json is True:
        emit_json({"ok": ok, "checks": [asdict(c) for c in checks]})
    else:
        console.print("[bold]Environment[/bold]")
        for c in env_checks:
            console.print(_doctor_line(c))
        console.print("\n[bold]Services[/bold]")
        if port_checks:
            for c in port_checks:
                console.print(_doctor_line(c))
        else:
            console.print("  [dim]○ XORCISE is not running — start it with: xorcise up[/dim]")
        if ok:
            console.print("\n[ok]No problems found.[/ok]")
        else:
            console.print(
                "\n[err]Fix the ✗ items above[/err], then re-run [value]xorcise doctor[/value]."
            )
    if not ok:
        raise typer.Exit(1)


def _await_exit(pid: int, timeout: float = 8.0, poll: float = 0.1) -> bool:
    """Block until `pid` is gone; return True if it exited within `timeout`, else False.

    Used by `down` so the server has actually released its listeners before we return —
    otherwise an immediate `up` races the still-terminating process (a live listener,
    which SO_REUSEADDR can't bypass)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        time.sleep(poll)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return True
    return False


def _stdin_is_interactive() -> bool:
    """Seam for tests: CliRunner swaps sys.stdin, so the guard resolves it at call time."""
    return sys.stdin.isatty()


@app.command(rich_help_panel="Getting started")
def down(
    keep_data: bool = typer.Option(
        False, "--keep-data", help="Preserve all ~/.xorcise state (stop only)."
    ),
    purge: bool = typer.Option(
        False, "--purge", help="Remove ALL ~/.xorcise state (config, db, runs). Destructive."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the --purge confirmation prompt."),
) -> None:
    """Stop XORCISE and clean up (safe to re-run).

    Default clears transient runtime state (keeps config + data); \
--keep-data keeps everything; --purge wipes all of ~/.xorcise \
(confirmation required unless --yes).
    """
    if keep_data and purge:
        err_console.print("[err]--keep-data and --purge are mutually exclusive[/err]")
        raise typer.Exit(2)
    if purge and not yes and not _stdin_is_interactive():
        # A prompt would hang a CI step or agent harness forever — fail fast instead.
        err_console.print(
            "[err]--purge needs confirmation — pass --yes in non-interactive use[/err]"
        )
        raise typer.Exit(2)

    pf = pid_file()
    running = pf.exists()
    if running:
        with (
            contextlib.suppress(ProcessLookupError, ValueError, PermissionError),
            step_progress("stopping XORCISE"),
        ):
            pid = int(pf.read_text())
            os.kill(pid, signal.SIGTERM)
            # Wait for it to actually exit (release its ports) so a following `up` isn't
            # racing a still-terminating server; escalate to SIGKILL if it won't stop.
            if not _await_exit(pid):
                os.kill(pid, signal.SIGKILL)
                _await_exit(pid, timeout=2.0)
        pf.unlink(missing_ok=True)
        runtime_ports_file().unlink(missing_ok=True)  # the record dies with the server
    # Reap orphaned per-run mission containers. Unconditional and label-driven, so it
    # also clears containers from abandoned runs (agent crashed before terminal) and prior sessions
    # — the server's in-process deploy map is gone once it is killed above. Runs BEFORE the
    # Headscale teardown so a reaped router never lingers pointing at a control plane we are
    # about to remove.
    from xorcise.core.config import get_settings
    from xorcise.core.rest.reap import reap_managed_containers

    with step_progress("reaping orphaned run containers"):
        reaped = reap_managed_containers(get_settings())
    if reaped:
        console.print(f"reaped {len(reaped)} orphaned run container(s)")
    # Tear down a locally-provisioned Headscale, unless preserving state. Only acts
    # when WE own it (the ownership marker) — an external control plane is left untouched.
    if not keep_data:
        _maybe_teardown_headscale()

    home = xorcise_home()
    if purge:
        if not yes and not typer.confirm(f"Remove ALL XORCISE state at {home}?"):
            console.print("aborted")
            raise typer.Exit(1)
        purge_home(home)
        console.print(f"purged {home}")
        return
    if not keep_data:
        clear_transient(home)
    # A live terminal gets the branded sign-off (the mirror of `up`'s banner);
    # piped/captured output keeps the historical plain line for scripts.
    if running and console.is_terminal:
        console.print(
            "[accent]⊕[/] stopped — [dim]Trust Evidence, not Claims. "
            "restart:[/dim] [value]xorcise up[/value]"
        )
    else:
        console.print("stopped — xorcise down" if running else "not running")
