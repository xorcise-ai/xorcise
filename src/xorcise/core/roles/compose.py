"""Resolve a role's compose profile path (LAYER: roles).

The compose profiles are the deploy-time mirror of ROLE_MANIFEST.toml; the
topology parity test cross-checks their services against the manifest.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]


def resolve(profile: str, root: Path | None = None) -> Path:
    """Return the path to deploy/compose/profiles/<profile>.yaml."""
    base = root or _REPO_ROOT
    return base / "deploy" / "compose" / "profiles" / f"{profile}.yaml"
