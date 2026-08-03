"""xorcise.core.roles.activate — import-and-mount only the active role's plane.

LAYER: roles. `activate(role)` imports ONLY that role's boot module (the sole
side-effecting import), builds its apps, then asserts no forbidden part-island
was imported — the runtime mirror of the import-linter walls. The authoritative
isolation proof is the subprocess test in tests/topology (a clean interpreter).
"""

from __future__ import annotations

import importlib
import sys

from xorcise.core.roles.boot import AppSpec
from xorcise.core.roles.registry import PART_ISLANDS, RoleError, load_manifest


class ForbiddenPlaneError(RoleError):
    """A role activation imported a part-island outside its declared plane."""


def _breaches(imported: set[str], forbidden: frozenset[str]) -> set[str]:
    """Part-islands that were imported but are forbidden for this role (pure)."""
    return imported & forbidden


def activate(role: str) -> list[AppSpec]:
    manifest = load_manifest()
    spec = manifest.role(role)  # UnknownRoleError if absent
    forbidden = manifest.forbidden_parts(role)

    before = set(sys.modules)
    boot = importlib.import_module(spec.boot)
    apps_fn = boot.apps
    specs: list[AppSpec] = apps_fn()
    newly_imported = (set(sys.modules) - before) & PART_ISLANDS
    breaches = _breaches(newly_imported, forbidden)
    if breaches:
        raise ForbiddenPlaneError(f"role '{role}' imported forbidden plane(s): {sorted(breaches)}")
    return specs
