"""Acceptance: the scripted agent ships in NO product artifact (tests-only)."""

from pathlib import Path

import tests.fixtures.scripted_agent as sa
import xorcise


def test_fixture_lives_under_tests() -> None:
    assert "tests" in Path(sa.__file__).parts


def test_scripted_agent_not_in_shipped_package() -> None:
    pkg_root = Path(xorcise.__file__).parent
    py_stems = {p.stem for p in pkg_root.rglob("*.py")}
    assert "scripted_agent" not in py_stems
