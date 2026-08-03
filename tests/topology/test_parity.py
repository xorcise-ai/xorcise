"""ROLE_MANIFEST.toml is the single source of truth; this suite fails CI naming
any drift between it and the pyproject extras, compose profiles, Dockerfile
targets, the .importlinter island list, and the live import graph (per role)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest
import yaml

from xorcise.core.roles import compose
from xorcise.core.roles.registry import PART_ISLANDS, load_manifest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = load_manifest()
ROLES = list(MANIFEST.roles)


# --- extras parity -----------------------------------------------------------
def test_every_role_extra_exists_in_pyproject() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = set(pyproject["project"]["optional-dependencies"])
    for role in MANIFEST.roles.values():
        for extra in role.extras:
            assert extra in declared, f"role {role.name}: extra '{extra}' missing from pyproject"


# --- compose parity ----------------------------------------------------------
def test_one_profile_per_role_no_orphans() -> None:
    profiles = {p.stem for p in (ROOT / "deploy/compose/profiles").glob("*.yaml")}
    expected = {r.compose_profile for r in MANIFEST.roles.values()}
    assert profiles == expected, f"profile drift: {profiles ^ expected}"


@pytest.mark.parametrize("role", ROLES)
def test_profile_services_match_manifest(role: str) -> None:
    spec = MANIFEST.role(role)
    profile = yaml.safe_load(compose.resolve(spec.compose_profile).read_text())
    assert set(profile.get("services", {})) == set(spec.compose_services)


# --- Dockerfile parity -------------------------------------------------------
def test_dockerfile_targets_match_roles() -> None:
    text = (ROOT / "Dockerfile").read_text()
    targets = set(re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", text, re.MULTILINE)) - {"base"}
    expected = {r.dockerfile_target for r in MANIFEST.roles.values()}
    assert targets == expected, f"Dockerfile target drift: {targets ^ expected}"


# --- .importlinter parity ----------------------------------------------------
def test_importlinter_walls_every_part_island() -> None:
    listed = {
        ln.strip()
        for ln in (ROOT / ".importlinter").read_text().splitlines()
        if ln.strip().startswith("xorcise.core.")
    }
    for part in PART_ISLANDS:
        assert part in listed, f"{part} is not walled in .importlinter"


def test_lint_imports_passes() -> None:
    r = subprocess.run(["lint-imports"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# --- import-graph isolation (the authoritative 'boot only this plane') --------
_DUMP = textwrap.dedent(
    """
    import json, sys
    from xorcise.core.roles.activate import activate
    activate({role!r})
    print(json.dumps([m for m in sys.modules if m.startswith("xorcise.core.")]))
    """
)


@pytest.mark.parametrize("role", ROLES)
def test_role_boots_only_its_plane(role: str) -> None:
    r = subprocess.run(
        [sys.executable, "-c", _DUMP.format(role=role)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    imported = set(json.loads(r.stdout.strip().splitlines()[-1]))
    leaked = imported & MANIFEST.forbidden_parts(role)
    assert not leaked, f"role {role} leaked forbidden plane(s): {sorted(leaked)}"


# --- injected-drift negative (proves the wall actually catches violations) ----
def test_injected_forbidden_plane_raises(tmp_path: Path) -> None:
    # A 'leaky' role boots role_all (imports the otel plane) but declares NO
    # modules, so forbidden_parts == every island incl. otel -> must raise.
    drifted = tmp_path / "ROLE_MANIFEST.toml"
    drifted.write_text(
        "[roles.leaky]\n"
        'boot = "xorcise.core.roles.boot.role_all"\n'
        "modules = []\n"
        "extras = []\n"
        'compose_profile = "all"\n'
        'compose_services = ["app"]\n'
        'dockerfile_target = "all"\n'
    )
    script = "from xorcise.core.roles.activate import activate; activate('leaky')"
    r = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "XORCISE_ROLE_MANIFEST": str(drifted)},
    )
    assert r.returncode != 0
    assert "ForbiddenPlaneError" in (r.stdout + r.stderr)


# --- module-health mirror parity ---------------------------------------------
def test_system_view_role_modules_mirrors_the_manifest_roles() -> None:
    """`rest/system_view.py::_ROLE_MODULES` decides which modules a role is expected to run, and
    therefore which ones the operator surfaces may paint red.

    It cannot read ROLE_MANIFEST.toml itself: .importlinter puts `roles` ABOVE `rest`, so
    importing the registry from the rest layer would break the layered contract. That makes it a
    hand-kept mirror — exactly the kind of duplicate this suite exists to stop drifting. This
    test is the CI gate: a role added to (or removed from) the manifest must be reflected there,
    or a brand-new role would silently report every module as "not on this host".
    """
    from xorcise.core.rest.system_view import _ROLE_MODULES

    assert set(_ROLE_MODULES) == set(ROLES), (
        "system_view._ROLE_MODULES has drifted from ROLE_MANIFEST.toml: "
        f"manifest={sorted(ROLES)} mirror={sorted(_ROLE_MODULES)}"
    )


def test_only_docker_owning_roles_are_expected_to_run_the_runner_modules() -> None:
    """The roles expected to run docker/headscale must be exactly the roles that get the REAL
    Docker and Headscale adapters (`_use_real_docker` / `_use_real_headscale` == {all, runner}).
    If these disagree, a host either shows a module it never drives or hides one it does."""
    from xorcise.core.rest.system_view import _ROLE_MODULES

    drives_docker = {role for role, mods in _ROLE_MODULES.items() if "docker" in mods}
    assert drives_docker == {"all", "runner"}
