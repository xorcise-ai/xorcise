"""xorcise.core.roles.registry — ROLE_MANIFEST.toml → typed activation sets.

LAYER: roles. Pure reader: no import side effects, no part imports.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

PART_ISLANDS: frozenset[str] = frozenset(
    {
        "xorcise.core.eval",
        "xorcise.core.runner",
        "xorcise.core.headscale",
        "xorcise.core.otel",
        "xorcise.core.catalog",
        "xorcise.core.missions",
        "xorcise.core.code",
    }
)

_MANIFEST_NAME = "ROLE_MANIFEST.toml"


class RoleError(Exception):
    """Base for role-activation errors."""


class UnknownRoleError(RoleError):
    """Raised for a role not present in ROLE_MANIFEST.toml."""


@dataclass(frozen=True)
class RoleSpec:
    name: str
    boot: str
    modules: frozenset[str]
    extras: tuple[str, ...]
    compose_profile: str
    compose_services: tuple[str, ...]
    dockerfile_target: str
    health: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    roles: dict[str, RoleSpec]

    def role(self, name: str) -> RoleSpec:
        try:
            return self.roles[name]
        except KeyError:
            raise UnknownRoleError(name) from None

    def forbidden_parts(self, name: str) -> frozenset[str]:
        return PART_ISLANDS - self.role(name).modules


def _locate_manifest() -> Path:
    override = os.environ.get("XORCISE_ROLE_MANIFEST")
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _MANIFEST_NAME
        if candidate.is_file():
            return candidate
    from importlib.resources import files

    packaged = files("xorcise").joinpath(_MANIFEST_NAME)
    if packaged.is_file():
        return Path(str(packaged))
    raise RoleError(f"{_MANIFEST_NAME} not found")


def load_manifest(path: Path | None = None) -> Manifest:
    manifest_path = path or _locate_manifest()
    data = tomllib.loads(manifest_path.read_text())
    roles: dict[str, RoleSpec] = {}
    for name, raw in data.get("roles", {}).items():
        roles[name] = RoleSpec(
            name=name,
            boot=raw["boot"],
            modules=frozenset(raw.get("modules", [])),
            extras=tuple(raw.get("extras", [])),
            compose_profile=raw["compose_profile"],
            compose_services=tuple(raw.get("compose_services", [])),
            dockerfile_target=raw["dockerfile_target"],
            health=tuple(raw.get("health", [])),
        )
    return Manifest(roles=roles)
