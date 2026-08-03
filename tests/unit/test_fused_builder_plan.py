import subprocess

import pytest

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
