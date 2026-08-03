from __future__ import annotations

from xorcise.core.roles import compose


def test_resolve_points_at_profile_file() -> None:
    p = compose.resolve("all")
    assert p.name == "all.yaml"
    assert p.parent.name == "profiles"


def test_resolve_existing_profile_exists() -> None:
    assert compose.resolve("control").is_file()
