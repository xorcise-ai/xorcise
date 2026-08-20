import dataclasses
from pathlib import Path

import pytest

from xorcise.core import agents, runs
from xorcise.core.catalog import StubCatalogSource
from xorcise.core.config import Settings
from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import EnvironmentSpec, MissionManifest, MissionMetadata
from xorcise.core.headscale import NetworkController, StubHeadscaleCli
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission
from xorcise.core.orchestration.clients.control import InProcessControlStub
from xorcise.core.orchestration.clients.headscale_client import HeadscaleFenceClient
from xorcise.core.orchestration.ports import NetworkFencePort
from xorcise.core.rest.mission_pull import MissionNotInCatalogError, PullDeps
from xorcise.core.rest.run_create import (
    NoAgentError,
    RunCreateDeps,
    _agent_facing_host,
    _otlp_endpoint,
    build_run_create_deps,
    create_run,
)
from xorcise.core.runner.docker import StubDockerDriver
from xorcise.core.runs.prompt import render_prompt_text


@pytest.fixture
def install_root(tmp_path: Path) -> Path:
    return tmp_path / "missions"


def _write_installed(install_root: Path, slug: str = "sqli-login", version: int = 1) -> None:
    root = install_root / slug
    root.mkdir(parents=True, exist_ok=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(
            mission_id=slug, name=slug, objective="Bypass the login.", type="lab"
        ),
        environment=EnvironmentSpec(),
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(
        InstalledMission(slug, root, manifest, ref, install_revision=version).to_record()
    )


def _deps(install_root: Path) -> RunCreateDeps:
    fence = HeadscaleFenceClient(
        NetworkController(
            StubHeadscaleCli(), router_tag="tag:router", orchestrator_user="orchestrator"
        )
    )
    return RunCreateDeps(
        control=InProcessControlStub(api_key="k"),
        fence=fence,
        api_key="k",
        install_root=install_root,
        login_server="https://headscale.local",
        base_network="10.200.0.0/16",
        cidr_prefix=24,
        default_budget=3600,
        pull=PullDeps(
            source=StubCatalogSource(enabled=True),
            driver=StubDockerDriver(),
            install_root=install_root,
        ),
    )


def test_rejects_when_agent_not_registered(migrated_home, install_root):
    with pytest.raises(NoAgentError):
        create_run(
            agent_name="ghost", mission_slug="x", budget_seconds=None, deps=_deps(install_root)
        )


def test_rejects_when_mission_not_installed_and_not_in_catalog(migrated_home, install_root):
    agents.register("alice", endpoint="http://a")
    with pytest.raises(MissionNotInCatalogError):
        create_run(
            agent_name="alice",
            mission_slug="absent",  # not installed and not in the catalog fixture
            budget_seconds=None,
            deps=_deps(install_root),
        )


def test_autopulls_uninstalled_library_mission(migrated_home, install_root):
    agents.register("alice", endpoint="http://a")  # no _write_installed — must auto-pull
    run, prompt = create_run(
        agent_name="alice",
        mission_slug="sqli-login",  # in the catalog fixture
        budget_seconds=None,
        deps=_deps(install_root),
    )
    assert run.mission == "sqli-login"
    assert prompt.run_id == run.run_id


def test_happy_path_is_1_1_1_with_budget_and_prompt(migrated_home, install_root):
    agent = agents.register("alice", endpoint="http://a")
    _write_installed(install_root)
    run, prompt = create_run(
        agent_name="alice",
        mission_slug="sqli-login",
        budget_seconds=900,
        deps=_deps(install_root),
    )
    assert run.agent_id == agent.id
    assert run.mission == "sqli-login"
    assert run.budget_seconds == 900
    assert prompt.join_key
    assert prompt.run_id == run.run_id
    assert prompt.objective == "Bypass the login."


def test_create_run_surfaces_static_ip_target_in_prompt(migrated_home, install_root):
    # with static_ips {web: {default: 10}} the prompt's Targets section names web at its
    # authored address on the carved /24 (no fabricated port).
    agents.register("alice", endpoint="http://a")
    root = install_root / "sqli-login"
    root.mkdir(parents=True, exist_ok=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(
            mission_id="sqli-login", name="sqli-login", objective="hit http://web:80", type="lab"
        ),
        environment=EnvironmentSpec(
            entry_networks=("default",), static_ips={"web": {"default": 10}}
        ),
    )
    ref = MissionRef(mission_id="sqli-login", image="xorcise/mission-sqli-login:0")
    (root / INSTALLED_FILE).write_text(
        InstalledMission("sqli-login", root, manifest, ref, install_revision=1).to_record()
    )
    _run, prompt = create_run(
        agent_name="alice",
        mission_slug="sqli-login",
        budget_seconds=None,
        deps=_deps(install_root),
    )
    web = next(t for t in prompt.targets if t.name == "web")
    assert web.host.endswith(".10") and web.port is None  # authored octet, not docker-sequential
    text = render_prompt_text(prompt)
    # the Targets section lists the bare routed IP (no service name), and no port is
    # fabricated when the manifest omits one.
    assert web.host in text and f"web  {web.host}" not in text and f"{web.host}:" not in text


def test_create_run_surfaces_attachments_in_prompt(migrated_home, install_root):
    # A mission that declares companion files must surface them into the mission
    # prompt so a prompt-only agent learns they exist and can mint the signed download link.
    from xorcise.core.contracts.mission import Attachment

    agents.register("alice", endpoint="http://a")
    root = install_root / "sqli-login"
    root.mkdir(parents=True, exist_ok=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(
            mission_id="sqli-login", name="sqli-login", objective="Bypass the login.", type="lab"
        ),
        environment=EnvironmentSpec(),
        attachments=(
            Attachment(name="notes.txt", path="files/notes.txt", media_type="text/plain"),
        ),
    )
    ref = MissionRef(mission_id="sqli-login", image="xorcise/mission-sqli-login:0")
    (root / INSTALLED_FILE).write_text(
        InstalledMission("sqli-login", root, manifest, ref, install_revision=1).to_record()
    )
    _run, prompt = create_run(
        agent_name="alice",
        mission_slug="sqli-login",
        budget_seconds=None,
        deps=_deps(install_root),
    )
    assert [a.name for a in prompt.attachments] == ["notes.txt"]
    assert prompt.attachments[0].media_type == "text/plain"
    assert "notes.txt" in render_prompt_text(prompt)


def test_airgapped_deps_flow_ca_and_host_alias_into_prompt(migrated_home, install_root):
    # the CA + host alias the deps carry (air-gapped) must reach the MissionPrompt so the
    # rendered connect recipe is self-sufficient.
    agents.register("alice", endpoint="http://a")
    _write_installed(install_root)
    deps = dataclasses.replace(
        _deps(install_root),
        ca_cert="-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n",
        extra_hosts=("headscale.local:172.17.0.1",),
    )
    _run, prompt = create_run(
        agent_name="alice", mission_slug="sqli-login", budget_seconds=None, deps=deps
    )
    assert prompt.ca_cert.startswith("-----BEGIN CERTIFICATE-----")
    assert prompt.host_alias == "headscale.local:172.17.0.1"


def test_run_control_key_minted_and_distinct_from_join(migrated_home, install_root):
    agents.register("alice", endpoint="http://a")
    _write_installed(install_root)
    _run, prompt = create_run(
        agent_name="alice",
        mission_slug="sqli-login",
        budget_seconds=None,
        deps=_deps(install_root),
    )
    assert prompt.run_control_key  # minted
    assert prompt.run_control_key != prompt.join_key  # distinct channels


def test_budget_defaults_when_unset(migrated_home, install_root):
    agents.register("alice", endpoint="http://a")
    _write_installed(install_root)
    run, _ = create_run(
        agent_name="alice",
        mission_slug="sqli-login",
        budget_seconds=None,
        deps=_deps(install_root),
    )
    assert run.budget_seconds == 3600


# ---------------------------------------------------------------------------
# OTLP/collector endpoint threading (advertise_host → LaunchProfile)
# ---------------------------------------------------------------------------


def test_otlp_endpoint_helper_populated() -> None:
    """_otlp_endpoint assembles the correct URL when advertise_host is set."""
    from xorcise.core.orchestration.clients.headscale_client import HeadscaleFenceClient

    base = RunCreateDeps(
        control=InProcessControlStub(api_key="k"),
        fence=HeadscaleFenceClient(
            NetworkController(StubHeadscaleCli(), router_tag="tag:router", orchestrator_user="orch")
        ),
        api_key="k",
        install_root=Path("/unused"),
        login_server="https://headscale.local",
        base_network="10.200.0.0/16",
        cidr_prefix=24,
        default_budget=3600,
        pull=PullDeps(
            source=StubCatalogSource(enabled=True),
            driver=StubDockerDriver(),
            install_root=Path("/unused"),
        ),
    )
    deps = dataclasses.replace(base, advertise_host="172.17.0.1", otlp_port=4318)
    assert _otlp_endpoint(deps) == "http://172.17.0.1:4318"


def test_otlp_endpoint_helper_empty_when_no_advertise_host() -> None:
    """_otlp_endpoint returns empty string when advertise_host is unset (back-compat path)."""
    from xorcise.core.orchestration.clients.headscale_client import HeadscaleFenceClient

    deps = RunCreateDeps(
        control=InProcessControlStub(api_key="k"),
        fence=HeadscaleFenceClient(
            NetworkController(StubHeadscaleCli(), router_tag="tag:router", orchestrator_user="orch")
        ),
        api_key="k",
        install_root=Path("/unused"),
        login_server="https://headscale.local",
        base_network="10.200.0.0/16",
        cidr_prefix=24,
        default_budget=3600,
        pull=PullDeps(
            source=StubCatalogSource(enabled=True),
            driver=StubDockerDriver(),
            install_root=Path("/unused"),
        ),
        advertise_host="",  # explicit empty — back-compat default
    )
    assert _otlp_endpoint(deps) == ""


def test_create_run_prompt_has_no_telemetry_env(migrated_home, install_root) -> None:
    """The rendered prompt carries no OTLP env even when advertise_host is set (harness
    owns telemetry). _otlp_endpoint still derives the collector URL for the harness seam."""
    agents.register("alice", endpoint="http://a")
    _write_installed(install_root)
    deps = dataclasses.replace(_deps(install_root), advertise_host="172.17.0.1", otlp_port=4318)
    assert _otlp_endpoint(deps) == "http://172.17.0.1:4318"  # seam still wires the collector URL
    _run, mission = create_run(
        agent_name="alice",
        mission_slug="sqli-login",
        budget_seconds=None,
        deps=deps,
    )
    rendered = render_prompt_text(mission)
    assert "OTEL_" not in rendered
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in rendered


# ---------------------------------------------------------------------------
# topology-resolved agent-facing host
# ---------------------------------------------------------------------------


def test_agent_facing_host_local_is_docker_internal():
    assert _agent_facing_host(Settings(deployment_topology="local")) == "host.docker.internal"


def test_agent_facing_host_distributed_uses_advertise_host():
    s = Settings(deployment_topology="distributed", headscale_advertise_host="10.1.2.3")
    assert _agent_facing_host(s) == "10.1.2.3"


def test_local_deps_point_otlp_at_docker_internal_and_skip_collector_carveout():
    deps = build_run_create_deps(
        Settings(deployment_topology="local", headscale_advertise_host="172.17.0.1"),
        use_docker=False,
    )
    assert deps.advertise_host == "host.docker.internal"
    # fence built with no collector carve-out in local (collector_addr empty)
    assert deps.fence._controller._collector_addr == ""  # type: ignore[attr-defined]


def test_local_login_server_stays_on_tailnet_host_not_docker_internal():
    # guard: the tailnet login server (per-run router + agent join recipe) must dial
    # the advertised tailnet host even in local — the router container cannot resolve the
    # host.docker.internal alias. Only the OTLP endpoint (advertise_host) uses host.docker.internal.
    # headscale_url="" forces the plain-HTTP derivation (no air-gapped TLS url short-circuit).
    deps = build_run_create_deps(
        Settings(
            deployment_topology="local", headscale_advertise_host="172.17.0.1", headscale_url=""
        ),
        use_docker=False,
    )
    assert deps.login_server == "http://172.17.0.1:8080"
    assert "host.docker.internal" not in deps.login_server
    assert deps.advertise_host == "host.docker.internal"  # OTLP path still uses the docker alias


def test_create_run_captures_agent_model_and_sandbox_ref(migrated_home, install_root):
    """Run row captures agent's declared model + mission image as conditions."""
    agents.register("model-agent", endpoint="http://a", model="claude-opus-4-8")
    _write_installed(install_root, slug="sqli-login")
    run, _prompt = create_run(
        agent_name="model-agent",
        mission_slug="sqli-login",
        budget_seconds=300,
        deps=_deps(install_root),
    )
    assert run.model == "claude-opus-4-8"
    assert run.sandbox_ref == "xorcise/mission-sqli-login:0"  # from _write_installed's MissionRef


def test_create_run_conditions_none_when_agent_has_no_model(migrated_home, install_root):
    """sandbox_ref is always set; model is None when agent declares none."""
    agents.register("plain-agent", endpoint="http://b")
    _write_installed(install_root, slug="sqli-login")
    run, _prompt = create_run(
        agent_name="plain-agent",
        mission_slug="sqli-login",
        budget_seconds=None,
        deps=_deps(install_root),
    )
    assert run.model is None
    assert run.sandbox_ref == "xorcise/mission-sqli-login:0"


def test_create_run_captures_agent_version_and_mission_version(migrated_home, install_root):
    """Run row captures agent_version + mission_version at create time.

    Exercises the non-default 'version M' path through the REST create_run: the installed
    mission carries version 2 (re-install bump), and both versions are asserted
    SYMMETRICALLY against the resolved agent/installed records — not magic literals."""
    from xorcise.core.missions.runtime import get_installed

    agent = agents.register("versioned-agent", endpoint="http://a")
    # Re-install the same slug so InstalledMission.install_revision becomes 2 (monotonic bump).
    _write_installed(install_root, slug="sqli-login", version=1)
    _write_installed(install_root, slug="sqli-login", version=2)
    installed = get_installed("sqli-login", install_root)
    assert installed is not None
    assert installed.install_revision == 2  # pin the 'revision M != 1' precondition

    run, _prompt = create_run(
        agent_name="versioned-agent",
        mission_slug="sqli-login",
        budget_seconds=300,
        deps=_deps(install_root),
    )
    assert run.agent_version == agent.version
    assert run.install_revision == installed.install_revision


def test_create_run_copies_artifact_provenance_from_installed_json(migrated_home, install_root):
    """§31: the run row copies the installed artifact's identity at create time — the creator
    SemVer, base SemVer, content hash, and exactly what this machine pulled (platform +
    digests) — read from installed.json, never re-resolved from any registry."""
    from xorcise.core.contracts.control import (
        InstalledBaseIdentity,
        InstalledImageIdentity,
        MissionInstallIdentity,
    )

    slug = "sqli-login"
    root = install_root / slug
    root.mkdir(parents=True, exist_ok=True)
    manifest = MissionManifest(
        schema_version="3.0",
        version="1.4.2",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="obj", type="lab"),
        environment=EnvironmentSpec(),
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    identity = MissionInstallIdentity(
        mission_version="1.4.2",
        mission_base_version="2.4.1",
        content_hash="ab" * 32,
        image=InstalledImageIdentity(
            release_ref="reg/xorcise/mis-sqli-login:1.4.2-base2.4.1",
            index_digest="sha256:idx",
            platform="linux/arm64",
            platform_digest="sha256:arm",
        ),
        mission_base=InstalledBaseIdentity(version="2.4.1", index_digest="sha256:base"),
        pulled_at="2026-08-19T00:00:00+00:00",
    )
    (root / INSTALLED_FILE).write_text(
        InstalledMission(slug, root, manifest, ref, identity=identity).to_record()
    )
    agents.register("prov-agent", endpoint="http://a")

    run, _prompt = create_run(
        agent_name="prov-agent",
        mission_slug=slug,
        budget_seconds=300,
        deps=_deps(install_root),
    )
    assert run.mission_version == "1.4.2"
    assert run.mission_base_version == "2.4.1"
    assert run.content_hash == "ab" * 32
    assert run.platform == "linux/arm64"
    assert run.index_digest == "sha256:idx"
    assert run.platform_digest == "sha256:arm"


def test_create_run_provenance_none_for_pre_contract_install(migrated_home, install_root):
    """A your_own fuse / pre-contract install has no artifact identity: the run records None,
    not fabricated values — absence is a fact about the artifact."""
    agents.register("legacy-agent", endpoint="http://a")
    _write_installed(install_root, slug="sqli-login", version=1)
    run, _prompt = create_run(
        agent_name="legacy-agent",
        mission_slug="sqli-login",
        budget_seconds=300,
        deps=_deps(install_root),
    )
    assert run.mission_version is None
    assert run.index_digest is None
    assert run.platform is None


def test_create_run_captures_source_agent_from_kind(migrated_home, install_root):
    """Run row snapshots agent.kind into source_agent at create time."""
    agents.register("openhands-agent", endpoint="http://a", kind="openhands")
    _write_installed(install_root, slug="sqli-login")
    run, _prompt = create_run(
        agent_name="openhands-agent",
        mission_slug="sqli-login",
        budget_seconds=300,
        deps=_deps(install_root),
    )
    assert run.source_agent == "openhands"


def test_create_run_source_agent_defaults_to_generic_when_kind_unset(migrated_home, install_root):
    """source_agent falls back to 'generic' when the agent declares no kind."""
    agents.register("kindless-agent", endpoint="http://b")
    _write_installed(install_root, slug="sqli-login")
    run, _prompt = create_run(
        agent_name="kindless-agent",
        mission_slug="sqli-login",
        budget_seconds=300,
        deps=_deps(install_root),
    )
    assert run.source_agent == "generic"


def test_distributed_deps_keep_xor182_collector_carveout(monkeypatch):
    # Hermetic: stub both real factories so no Docker daemon or Headscale container is needed.
    # use_docker=True is kept so fence_real=True (the carve-out path under test) is exercised.
    monkeypatch.setattr(
        "xorcise.core.rest.mission_pull._real_docker_driver",
        lambda: StubDockerDriver(),
    )
    monkeypatch.setattr(
        "xorcise.core.rest.run_create._real_headscale_cli",
        lambda settings: StubHeadscaleCli(),
    )
    deps = build_run_create_deps(
        Settings(deployment_topology="distributed", headscale_advertise_host="172.17.0.1"),
        use_docker=True,
    )
    assert deps.advertise_host == "172.17.0.1"
    assert deps.fence._controller._collector_addr == "172.17.0.1"  # type: ignore[attr-defined]


def test_create_run_failure_releases_the_reserved_subnet(migrated_home, install_root):
    # the subnet is reserved as a DB row before deploy; if the create then fails, the
    # reservation must be released so the subnet is not leaked (no orphaned 'created' row).
    agents.register("alice", endpoint="http://a")
    _write_installed(install_root)

    class _BoomFence(NetworkFencePort):
        def create_run_network(self, run_id, agent_user, entry_cidrs, *, agent_ingress=False):
            raise RuntimeError("boom")

        def teardown_run_network(self, run_id): ...

        def reconcile_acl(self): ...

    deps = dataclasses.replace(_deps(install_root), fence=_BoomFence())
    with pytest.raises(RuntimeError, match="boom"):
        create_run(agent_name="alice", mission_slug="sqli-login", budget_seconds=None, deps=deps)
    assert runs.active_cidrs() == set()  # reservation released — no leaked subnet or orphan row


class _RecordingFence(NetworkFencePort):
    """Wraps a fence, recording the entry_cidrs each run is handed (collision checks)."""

    def __init__(self, inner, sink: list[tuple[str, ...]], lock=None) -> None:
        self._inner = inner
        self._sink = sink
        self._lock = lock

    def create_run_network(self, run_id, agent_user, entry_cidrs, *, agent_ingress=False):
        rec = tuple(entry_cidrs)
        if self._lock is not None:
            with self._lock:
                self._sink.append(rec)
        else:
            self._sink.append(rec)
        return self._inner.create_run_network(run_id, agent_user, entry_cidrs)

    def teardown_run_network(self, run_id):
        self._inner.teardown_run_network(run_id)

    def reconcile_acl(self):
        self._inner.reconcile_acl()


def test_concurrent_create_run_allocates_distinct_cidrs(migrated_home, install_root, monkeypatch):
    # N creates racing (all reach allocation before any persists) must each get a DISTINCT
    # /24 — else two per-run routers advertise the same subnet and an agent's tailscale connection
    # fails. Count-based allocation with an empty allocated set hands them the same subnet; the
    # atomic reservation (lock + in-flight reserved set) keeps them distinct.
    import threading

    n = 6
    agents.register("alice", endpoint="http://a")
    _write_installed(install_root)
    recorded: list[tuple[str, ...]] = []
    deps = dataclasses.replace(
        _deps(install_root),
        fence=_RecordingFence(_deps(install_root).fence, recorded, lock=threading.Lock()),
    )

    # All threads sync at the first step (agents.get, before allocation) so they contend for a
    # subnet simultaneously. The barrier is BEFORE the allocation lock, so the fix serializes
    # cleanly (no deadlock); the buggy count-based path hands several of them the same /24.
    barrier = threading.Barrier(n, timeout=5)
    real_get = agents.get

    def _barriered_get(name):
        result = real_get(name)
        barrier.wait()
        return result

    monkeypatch.setattr(agents, "get", _barriered_get)

    errors: list[BaseException] = []

    def _make() -> None:
        try:
            create_run(
                agent_name="alice",
                mission_slug="sqli-login",
                budget_seconds=None,
                deps=deps,
            )
        except BaseException as exc:  # noqa: BLE001 — surface cross-thread failures in the assert
            errors.append(exc)

    threads = [threading.Thread(target=_make) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(recorded) == n, f"all runs should allocate; got {recorded} errors={errors}"
    assert len({r for r in recorded}) == n, f"concurrent runs collided: {sorted(recorded)}"


def test_create_run_avoids_a_subnet_a_leftover_docker_network_holds(migrated_home, install_root):
    # Root cause of the joined-but-target-dead incident: allocation excluded only the DB's
    # non-terminal runs, never a LEFTOVER Docker network whose run the DB no longer tracks. If such
    # a network still owns 10.200.1.0/24, reusing it makes the new mission deploy collide on the
    # subnet and the target never comes up (the agent joins the tailnet but has nothing to reach).
    # Reconciling live Docker subnets into the allocator skips the reuse.
    agents.register("alice", endpoint="http://a")
    _write_installed(install_root)
    deps = dataclasses.replace(_deps(install_root), live_subnets=lambda: {"10.200.1.0/24"})
    create_run(agent_name="alice", mission_slug="sqli-login", budget_seconds=None, deps=deps)
    assert runs.active_cidrs() == {"10.200.2.0/24"}  # skipped the leftover-held .1.0/24


def test_real_deps_reconcile_live_docker_subnets_into_allocation(monkeypatch):
    # build_run_create_deps wires live_subnets to the docker driver, filtered to the run pool: a
    # leftover network inside base_network is masked; docker's own out-of-pool bridges are not.
    from xorcise.core.headscale.cidr import overlapping_subnets

    driver = StubDockerDriver()
    driver.network_cidrs = {"10.200.5.0/24", "172.17.0.0/16"}
    monkeypatch.setattr("xorcise.core.rest.mission_pull._real_docker_driver", lambda: driver)
    monkeypatch.setattr(
        "xorcise.core.rest.run_create._real_headscale_cli", lambda settings: StubHeadscaleCli()
    )
    deps = build_run_create_deps(
        Settings(deployment_topology="local", headscale_advertise_host="172.17.0.1"),
        use_docker=True,
    )
    live = deps.live_subnets()
    assert "10.200.5.0/24" in live  # a leftover run network is masked
    assert "172.17.0.0/16" not in live  # docker's own bridge is outside the run pool
    assert live == overlapping_subnets(
        deps.base_network, deps.cidr_prefix, {"10.200.5.0/24", "172.17.0.0/16"}
    )


def test_stub_deps_have_empty_live_subnets(install_root):
    # Stub/unit path has no docker to enumerate — live_subnets defaults to empty so existing unit
    # tests (and the allocator) are unaffected.
    deps = build_run_create_deps(Settings(deployment_topology="local"), use_docker=False)
    assert deps.live_subnets() == set()


def test_create_run_persists_entry_cidrs_for_acl(migrated_home, install_root):
    # the carved entry subnets are persisted so the ACL can be rendered from the DB.
    agents.register("alice", endpoint="http://a")
    _write_installed(install_root)
    run, _ = create_run(
        agent_name="alice",
        mission_slug="sqli-login",
        budget_seconds=None,
        deps=_deps(install_root),
    )
    nets = {rid: cidrs for rid, cidrs, _ in runs.active_run_networks()}
    assert run.run_id in nets and nets[run.run_id]  # non-empty carved entry cidrs


def test_real_deps_wire_the_acl_active_provider(monkeypatch):
    # the real fence renders the ACL from the DB-authoritative provider.
    from xorcise.core.rest.run_create import _acl_active_provider

    monkeypatch.setattr(
        "xorcise.core.rest.mission_pull._real_docker_driver", lambda: StubDockerDriver()
    )
    monkeypatch.setattr(
        "xorcise.core.rest.run_create._real_headscale_cli", lambda settings: StubHeadscaleCli()
    )
    deps = build_run_create_deps(
        Settings(deployment_topology="local", headscale_advertise_host="172.17.0.1"),
        use_docker=True,
    )
    assert deps.fence._controller._active_provider is _acl_active_provider  # type: ignore[attr-defined]


def test_local_stub_deps_have_no_acl_provider():
    # Stub/unit path keeps the in-process view (no DB provider) so unit tests are unaffected.
    deps = build_run_create_deps(Settings(deployment_topology="local"), use_docker=False)
    assert deps.fence._controller._active_provider is None  # type: ignore[attr-defined]


def test_create_run_persists_the_join_key(migrated_home, install_root):
    # the fence-minted one-time join key is persisted on the run so GET /connect can serve
    # it — it is no longer only embedded in the prompt text.
    agents.register("alice", endpoint="http://a")
    _write_installed(install_root)
    run, mission = create_run(
        agent_name="alice",
        mission_slug="sqli-login",
        budget_seconds=None,
        deps=_deps(install_root),
    )
    assert runs.get_join_key(run.run_id) == mission.join_key
    assert runs.get_join_key(run.run_id)  # non-empty (the stub fence mints a key)
