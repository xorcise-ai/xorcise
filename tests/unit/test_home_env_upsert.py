"""set_env_vars: idempotent upsert of KEY=value lines in ~/.xorcise/.env (XOR config surface)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from xorcise.core.home import set_env_vars


def _read(env: Path) -> list[str]:
    return env.read_text().splitlines()


@pytest.mark.unit
def test_creates_env_0600_with_keys(tmp_path: Path) -> None:
    set_env_vars(tmp_path, {"XORCISE_MODEL_KEY": "sk-abc", "XORCISE_MODEL_NAME": "gpt-4o-mini"})
    env = tmp_path / ".env"
    assert env.exists()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    lines = _read(env)
    assert "XORCISE_MODEL_KEY=sk-abc" in lines
    assert "XORCISE_MODEL_NAME=gpt-4o-mini" in lines


@pytest.mark.unit
def test_preserves_unrelated_lines_and_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# a comment\nOTHER_VAR=keepme\n")
    set_env_vars(tmp_path, {"XORCISE_MODEL_KEY": "sk-abc"})
    lines = _read(env)
    assert "# a comment" in lines
    assert "OTHER_VAR=keepme" in lines
    assert "XORCISE_MODEL_KEY=sk-abc" in lines


@pytest.mark.unit
def test_reupsert_updates_in_place_no_dupes(tmp_path: Path) -> None:
    set_env_vars(tmp_path, {"XORCISE_MODEL_KEY": "sk-old"})
    set_env_vars(tmp_path, {"XORCISE_MODEL_KEY": "sk-new"})
    lines = _read(tmp_path / ".env")
    assert lines.count("XORCISE_MODEL_KEY=sk-new") == 1
    assert "XORCISE_MODEL_KEY=sk-old" not in lines


@pytest.mark.unit
def test_none_value_removes_line(tmp_path: Path) -> None:
    set_env_vars(tmp_path, {"XORCISE_MODEL_KEY": "sk-abc"})
    set_env_vars(tmp_path, {"XORCISE_MODEL_KEY": None})
    lines = _read(tmp_path / ".env")
    assert not any(ln.startswith("XORCISE_MODEL_KEY=") for ln in lines)


@pytest.mark.unit
def test_mode_stays_0600_on_update(tmp_path: Path) -> None:
    set_env_vars(tmp_path, {"XORCISE_MODEL_KEY": "sk-abc"})
    set_env_vars(tmp_path, {"XORCISE_MODEL_NAME": "gpt-4o-mini"})
    env = tmp_path / ".env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
