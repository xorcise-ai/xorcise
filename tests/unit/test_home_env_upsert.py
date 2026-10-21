"""set_env_vars: idempotent upsert of KEY=value lines in ~/.xorcise/.env (XOR config surface)."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

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


# --- the key is never readable by others, even mid-write ---------------------------------------
#
# Found while fixing #160: the writer used write_text then chmod(0o600), so under a 022 umask a new
# .env holding the API key was 0644 until the chmod landed.


@pytest.mark.unit
@pytest.mark.parametrize("existing", [False, True])
def test_the_key_is_never_written_into_a_file_others_can_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    """Catch the file at the moment the key goes into it. Its mode must already be 0600, under
    the permissive umask that made the old window real, and for a file an older version left
    0644 as well as a new one."""
    import os

    env = tmp_path / ".env"
    if existing:
        env.write_text("OTHER=1\n")
        env.chmod(0o644)
    modes_at_write: list[int] = []
    real_fdopen = os.fdopen

    def spying_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        f = real_fdopen(fd, *args, **kwargs)
        real_write = f.write

        def write(text: str) -> int:
            modes_at_write.append(stat.S_IMODE(os.fstat(fd).st_mode))
            return int(real_write(text))

        f.write = write
        return f

    monkeypatch.setattr(os, "fdopen", spying_fdopen)
    old_umask = os.umask(0o022)
    try:
        set_env_vars(tmp_path, {"XORCISE_MODEL_KEY": "sk-secret"})
    finally:
        os.umask(old_umask)

    assert modes_at_write == [0o600]
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "XORCISE_MODEL_KEY=sk-secret" in _read(env)


@pytest.mark.unit
def test_a_symlinked_env_is_written_through_not_replaced(tmp_path: Path) -> None:
    real = tmp_path / "secrets" / "xorcise.env"
    real.parent.mkdir()
    real.write_text("OTHER=1\n")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").symlink_to(real)

    set_env_vars(home, {"XORCISE_MODEL_KEY": "sk-abc"})

    assert (home / ".env").is_symlink()
    assert _read(real) == ["OTHER=1", "XORCISE_MODEL_KEY=sk-abc"]


@pytest.mark.unit
def test_the_env_file_is_rewritten_in_place(tmp_path: Path) -> None:
    """Same inode, so its owner and any hard link to it survive, and nothing is left beside it."""
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n")
    linked = tmp_path / "linked.env"
    linked.hardlink_to(env)
    inode = env.stat().st_ino

    set_env_vars(tmp_path, {"XORCISE_MODEL_KEY": "sk-abc"})

    assert env.stat().st_ino == inode
    assert "XORCISE_MODEL_KEY=sk-abc" in _read(linked)
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env", "linked.env"]
