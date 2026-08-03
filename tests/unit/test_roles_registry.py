from __future__ import annotations

import pytest

from xorcise.core.roles.registry import (
    PART_ISLANDS,
    RoleSpec,
    UnknownRoleError,
    load_manifest,
)

ROLES = {"all", "control", "runner", "headscale", "collector"}


def test_loads_every_role() -> None:
    m = load_manifest()
    assert set(m.roles) == ROLES
    assert isinstance(m.role("all"), RoleSpec)


def test_all_role_modules_and_targets() -> None:
    spec = load_manifest().role("all")
    assert spec.boot == "xorcise.core.roles.boot.role_all"
    assert "xorcise.core.otel" in spec.modules
    assert spec.extras == ("all",)
    assert spec.compose_profile == "all"
    assert spec.dockerfile_target == "all"
    # Ports are NOT in the manifest — xorcise.core.config is the single source.
    assert not hasattr(spec, "ports")


def test_forbidden_parts_are_derived() -> None:
    m = load_manifest()
    forbidden = m.forbidden_parts("control")
    assert "xorcise.core.runner" in forbidden
    assert "xorcise.core.otel" in forbidden
    assert "xorcise.core.headscale" in forbidden
    assert "xorcise.core.code" in forbidden
    assert "xorcise.core.eval" not in forbidden
    assert forbidden <= PART_ISLANDS


def test_all_role_forbids_nothing_extra() -> None:
    forbidden = load_manifest().forbidden_parts("all")
    assert forbidden == frozenset({"xorcise.core.code"})


def test_unknown_role_raises() -> None:
    with pytest.raises(UnknownRoleError):
        load_manifest().role("nope")
