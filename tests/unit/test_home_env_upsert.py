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


@pytest.mark.unit
# \n and \r, plus separators the writer's own splitlines() splits on that a search for those two
# misses — the gap the first version of this fix had.
@pytest.mark.parametrize("brk", ["\n", "\r", "\r\n", "\v", "\x1e", "\x85", "\u2028", "\u2029"])
def test_a_value_with_a_line_break_is_refused_and_the_file_is_untouched(
    tmp_path: Path, brk: str
) -> None:
    """#160: written unquoted, the break would split the line and leave a junk line that every
    later write preserves. Refused before anything is written, naming the variable but never the
    value, which may be a secret."""
    env = tmp_path / ".env"
    env.write_text("XORCISE_MODEL_NAME=before\n")

    with pytest.raises(ValueError, match="XORCISE_MODEL_KEY must be a single line") as refused:
        set_env_vars(
            tmp_path, {"XORCISE_MODEL_NAME": "after", "XORCISE_MODEL_KEY": f"sk-1{brk}sk-2"}
        )

    assert "sk-1" not in str(refused.value)
    assert env.read_text() == "XORCISE_MODEL_NAME=before\n"  # not even the valid half
