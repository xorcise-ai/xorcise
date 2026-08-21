"""Static (attachment-only) run creation (static-mission-support).

A static mission has no runtime environment, so creating its run must never touch the Docker
deploy seam or the Headscale fence. These tests inject seams that EXPLODE if called, proving the
static path is free of both, and assert the run row + mission prompt come out right."""

from __future__ import annotations

from pathlib import Path

from xorcise.core import agents, runs
from xorcise.core.catalog import StubCatalogSource
from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import Attachment, MissionManifest, MissionMetadata
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission
from xorcise.core.rest.mission_pull import PullDeps
from xorcise.core.rest.run_create import RunCreateDeps, create_run
from xorcise.core.runner.docker import StubDockerDriver
from xorcise.core.runs.prompt import render_prompt_text


class _BoomControl:
    """A ControlPort whose every method explodes — deploy/teardown must NOT run for a static run."""

    def deploy(self, request: object, *, credential: str) -> object:
        raise AssertionError("control.deploy must not run for a static mission")

    def teardown(self, run_id: str, *, credential: str) -> None:
        raise AssertionError("control.teardown must not run during static run creation")

    def status(self, run_id: str, *, credential: str) -> object:
        raise AssertionError("control.status must not run for a static mission")


class _BoomFence:
    """A NetworkFencePort whose every method explodes — Headscale must NOT be touched for static."""

    def create_run_network(self, run_id: str, agent_user: str, entry_cidrs: object) -> object:
        raise AssertionError("fence.create_run_network must not run for a static mission")

    def reconcile_acl(self) -> None:
        raise AssertionError("fence.reconcile_acl must not run for a static mission")

    def teardown_run_network(self, run_id: str) -> None:
        raise AssertionError("fence.teardown_run_network must not run during static run creation")


def _write_installed_static(install_root: Path, slug: str = "derelict-manifest") -> None:
    root = install_root / slug
    root.mkdir(parents=True, exist_ok=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(
            mission_id=slug,
            name=slug,
            objective="Reverse the binary; submit the flag.",
            type="static",
        ),
        environment=None,
        attachments=(
            Attachment(name="attachment.zip", path="attachment.zip", media_type="application/zip"),
        ),
    )
    ref = MissionRef(mission_id=slug, image="")  # no fused image for a static mission
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())


def _deps(install_root: Path) -> RunCreateDeps:
    return RunCreateDeps(
        control=_BoomControl(),  # type: ignore[arg-type]  # deliberately-minimal exploding stub
        fence=_BoomFence(),  # type: ignore[arg-type]  # deliberately-minimal exploding stub
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
        advertise_host="host.docker.internal",
    )


def test_static_run_created_without_docker_or_headscale(migrated_home, tmp_path: Path) -> None:
    install_root = tmp_path / "missions"
    agents.register("alice", endpoint="http://a")
    _write_installed_static(install_root)
    run, prompt = create_run(
        agent_name="alice",
        mission_slug="derelict-manifest",
        budget_seconds=900,
        deps=_deps(install_root),  # boom seams: any Docker/Headscale touch raises
    )
    assert run.mission == "derelict-manifest"
    assert run.budget_seconds == 900
    assert prompt.run_id == run.run_id
    assert prompt.join_key == ""  # no tailnet key minted
    assert prompt.targets == ()  # no targets resolved


def test_static_prompt_has_attachments_no_targets(migrated_home, tmp_path: Path) -> None:
    install_root = tmp_path / "missions"
    agents.register("alice", endpoint="http://a")
    _write_installed_static(install_root)
    _run, prompt = create_run(
        agent_name="alice",
        mission_slug="derelict-manifest",
        budget_seconds=None,
        deps=_deps(install_root),
    )
    text = render_prompt_text(prompt)
    assert "/join.sh" not in text  # no tailnet join
    assert "Targets (" not in text  # no targets section
    assert "POST" in text and "/artifacts" in text  # artifact submission
    assert "/complete" in text  # termination
    assert "/attachments/<name>" in text and "attachment.zip" in text  # attachment retrieval


def test_static_run_persists_row_with_no_network(migrated_home, tmp_path: Path) -> None:
    install_root = tmp_path / "missions"
    agents.register("alice", endpoint="http://a")
    _write_installed_static(install_root)
    run, _prompt = create_run(
        agent_name="alice",
        mission_slug="derelict-manifest",
        budget_seconds=None,
        deps=_deps(install_root),
    )
    assert runs.get_prompt(run.run_id)  # the mission prompt was persisted
    assert run.run_id not in {cidr for cidr in runs.active_cidrs()}  # holds no subnet
