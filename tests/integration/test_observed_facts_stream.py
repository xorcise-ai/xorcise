"""Observed facts are captured at run-create independent of any agent self-report.

The second evidence stream does NOT depend on OTel — no trace is emitted in this test, yet the
run-control facts projector + the captured network facts are both available for the run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xorcise.core import agents
from xorcise.core.catalog import StubCatalogSource
from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import EnvironmentSpec, MissionManifest, MissionMetadata
from xorcise.core.headscale import NetworkController, StubHeadscaleCli
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission
from xorcise.core.orchestration.clients.control import InProcessControlStub
from xorcise.core.orchestration.clients.headscale_client import HeadscaleFenceClient
from xorcise.core.rest.mission_pull import PullDeps
from xorcise.core.rest.run_create import RunCreateDeps, create_run
from xorcise.core.runner.docker import StubDockerDriver
from xorcise.core.runs.observed import InMemoryObservedFactsStore, run_control_facts


def _write_installed(install_root: Path, slug: str = "sqli-login") -> None:
    root = install_root / slug
    root.mkdir(parents=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(
            mission_id=slug, name=slug, objective="Bypass the login.", type="lab"
        ),
        environment=EnvironmentSpec(),
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())


def _deps(install_root: Path, observed: InMemoryObservedFactsStore) -> RunCreateDeps:
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
        observed=observed,
    )


@pytest.mark.integration
def test_network_facts_captured_at_run_create_without_any_otel(migrated_home, tmp_path):
    install_root = tmp_path / "missions"
    agents.register("alice", endpoint="http://a")
    _write_installed(install_root)
    observed = InMemoryObservedFactsStore()

    run, _prompt = create_run(
        agent_name="alice",
        mission_slug="sqli-login",
        budget_seconds=None,
        deps=_deps(install_root, observed),
    )

    facts = observed.list_for_run(run.run_id)
    names = {f.name for f in facts}
    assert "entry-cidrs" in names  # the enforced boundary config was recorded
    assert "join" in names  # the network-lifecycle event was recorded
    assert all(f.run_id == run.run_id for f in facts)
    # no auth/router secret leaked into any fact
    assert all("tskey" not in f.value.lower() for f in facts)


@pytest.mark.integration
def test_target_facts_recorded_at_run_create_for_mission_static_ips(migrated_home, tmp_path):
    """run_create persists a kind="target" name->IP fact per mission target, so the
    run-control /mission brief can resolve the <name-target-ip-> placeholder."""
    install_root = tmp_path / "missions"
    agents.register("alice", endpoint="http://a")
    root = install_root / "idor"
    root.mkdir(parents=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(
            mission_id="idor", name="idor", objective="hit the target", type="lab"
        ),
        environment=EnvironmentSpec(
            entry_networks=("default",), static_ips={"web": {"default": 10}}
        ),
    )
    ref = MissionRef(mission_id="idor", image="xorcise/mission-idor:0")
    (root / INSTALLED_FILE).write_text(InstalledMission("idor", root, manifest, ref).to_record())
    observed = InMemoryObservedFactsStore()

    run, _prompt = create_run(
        agent_name="alice",
        mission_slug="idor",
        budget_seconds=None,
        deps=_deps(install_root, observed),
    )

    targets = {f.name: f.value for f in observed.list_for_run(run.run_id) if f.kind == "target"}
    assert "web" in targets and targets["web"].endswith(".10")  # authored static IP, recorded


@pytest.mark.integration
def test_run_control_facts_available_for_a_no_submission_run():
    # A run with no submissions still projects deterministic run-control facts (no self-report).
    assert run_control_facts([]) == {
        "submission-count": 0,
        "artifact-count": 0,
        "flag-submitted": False,
        "intel-count": 0,
        "completed": False,
    }
