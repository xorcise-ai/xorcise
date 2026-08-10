import re
import subprocess

import pytest
import yaml

from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
)
from xorcise.core.runner.docker import build as build_mod
from xorcise.core.runner.docker.build import (
    BASE_IMAGE,
    _base_context,
    ensure_base_image,
    fused_tag,
    plan_inner_images,
)


def test_fused_tag_is_stable_per_mission_not_code_version():
    # the fused tag is per-mission + STABLE — independent of the setuptools-scm code
    # version — so the install record survives code commits (a new commit no longer changes the
    # tag and strands the recorded image). No registry.
    assert fused_tag("sqli-login") == "xorcise/mission-sqli-login:local"
    assert fused_tag("idor-accounts") == "xorcise/mission-idor-accounts:local"


def test_plan_splits_build_vs_image_services():
    compose = {
        "services": {
            "web": {"build": "./services/web"},
            "cache": {"image": "redis:7"},
        }
    }
    specs = {s.service: s for s in plan_inner_images(compose)}
    assert specs["web"].build_context == "./services/web" and specs["web"].image is None
    assert specs["cache"].image == "redis:7" and specs["cache"].build_context is None


def test_plan_handles_dict_build_context():
    compose = {"services": {"web": {"build": {"context": "./web", "dockerfile": "Dockerfile"}}}}
    (spec,) = plan_inner_images(compose)
    assert spec.build_context == "./web"


def test_plan_empty_compose_is_empty():
    assert plan_inner_images({}) == ()


def test_base_context_resolves_to_a_real_dockerfile():
    # Dev/editable checkout falls back to containers/mission-base; built wheel ships it as
    # package data. Either way the resolved context must contain a Dockerfile.
    ctx = _base_context()
    assert (ctx / "Dockerfile").is_file()


def test_ensure_base_image_skips_when_present(monkeypatch):
    monkeypatch.setattr(build_mod, "_image_present", lambda ref: True)

    def _boom(*a, **k):
        raise AssertionError("must not docker build when base image already present")

    monkeypatch.setattr(subprocess, "run", _boom)
    ensure_base_image()  # no-op, no build


def test_ensure_base_image_builds_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(build_mod, "_image_present", lambda ref: False)
    monkeypatch.setattr(build_mod, "_base_context", lambda: tmp_path)
    calls: list[list[str]] = []

    def _record(cmd, **k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", _record)
    ensure_base_image()
    assert calls == [["docker", "build", "-t", BASE_IMAGE, str(tmp_path)]]


def test_ensure_base_image_propagates_build_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(build_mod, "_image_present", lambda ref: False)
    monkeypatch.setattr(build_mod, "_base_context", lambda: tmp_path)

    def _fail(cmd, **k):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", _fail)
    with pytest.raises(subprocess.CalledProcessError):
        ensure_base_image()


def _manifest_with_compose(tmp_path) -> MissionManifest:
    """A minimal lab manifest whose compose has one pull-only service (no build context)."""
    (tmp_path / "docker-compose.yml").write_text(
        yaml.safe_dump({"services": {"web": {"image": "nginx:alpine"}}})
    )
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id="m1", name="m1", objective="Solve it.", type="lab"),
        environment=EnvironmentSpec(),
    )


def test_router_is_pinned_and_baked_under_the_canonical_tag(monkeypatch, tmp_path):
    """The router must be PULLED pinned but BAKED under netoverride.ROUTER_IMAGE.

    Two failure modes this guards, which pull in opposite directions:
      * pulling the floating `:stable` means re-fusing an old mission bakes whatever that tag
        means today — the mission is no longer reproducible;
      * baking under anything OTHER than the canonical tag means the per-run net-override asks
        compose for an image images.tar does not contain, and the mission dies at `up` on the
        hermetic inner daemon (no inner pull at deploy).
    Satisfying one by breaking the other is the easy mistake, so both are asserted together.
    """
    from xorcise.core.runner.docker.build import ROUTER_PIN
    from xorcise.core.runner.netoverride import ROUTER_IMAGE

    assert ROUTER_PIN != ROUTER_IMAGE, "the pull must be pinned, not the floating deploy tag"
    assert re.fullmatch(r"tailscale/tailscale:v\d+\.\d+\.\d+", ROUTER_PIN), ROUTER_PIN

    calls: list[list[str]] = []

    def _run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(build_mod, "ensure_base_image", lambda: None)

    manifest = _manifest_with_compose(tmp_path)
    build_mod.FusedImageBuilder().build(tmp_path, manifest)

    assert ["docker", "pull", ROUTER_PIN] in calls, "router must be pulled by its pin"
    assert ["docker", "tag", ROUTER_PIN, ROUTER_IMAGE] in calls, "pin must be re-tagged canonical"
    save = next(c for c in calls if c[:2] == ["docker", "save"])
    assert ROUTER_IMAGE in save, "images.tar must carry the router under the canonical tag"
    assert ROUTER_PIN not in save, "the pin must not be what lands in images.tar"
