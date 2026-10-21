"""xorcise.core.home — ~/.xorcise resolution (precedence + pinned bootstrap).

LAYER: APPLICATION (domain module). Side-effect-free path resolution; ``init_home`` creates dirs.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from xorcise.core.config import (
    HEADSCALE_PORT,
    HOST,
    OTLP_PORT,
    REST_PORT,
    RUNNER_PORT,
)


def xorcise_home() -> Path:
    """Resolve XORCISE_HOME → default ~/.xorcise (no I/O)."""
    override = os.environ.get("XORCISE_HOME")
    return Path(override) if override else Path.home() / ".xorcise"


def pid_file() -> Path:
    """Path to the running server's PID file."""
    return xorcise_home() / "xorcise.pid"


def init_home() -> Path:
    """Create the home dir (idempotent)."""
    home = xorcise_home()
    (home / "logs").mkdir(parents=True, exist_ok=True)
    return home


def runtime_ports_file() -> Path:
    """Path to the runtime-resolved port record (written by `up`, read by sibling CLIs)."""
    return xorcise_home() / "runtime-ports.json"


def write_runtime_ports(ports: dict[str, int]) -> None:
    """Record the ports the server actually bound (auto-increment may move them off config).

    Written by `up` next to the pid file; sibling CLI processes (`ui`/`status`/RestClient)
    overlay it over settings while the pid file exists.
    """
    path = runtime_ports_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ports))


def read_runtime_ports() -> dict[str, int] | None:
    """The server's recorded runtime ports, or None when absent/unreadable (use config)."""
    try:
        raw = json.loads(runtime_ports_file().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    ports: dict[str, int] = {
        k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, int)
    }
    return ports or None


# Runtime-only subpaths cleared by a default `down`; config + db are durable and never listed here.
_TRANSIENT = ("logs", "runtime-ports.json")


def clear_transient(home: Path) -> None:
    """Remove the transient runtime set under home; keep config + db."""
    for name in _TRANSIENT:
        p = home / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()


def purge_home(home: Path) -> None:
    """Remove the entire ~/.xorcise (full wipe). Used by `down --purge` and scripts/reset.py."""
    if home.exists():
        shutil.rmtree(home, ignore_errors=True)


_DEFAULT_CONFIG_TOML = (
    "# XORCISE config. Environment (XORCISE_*) overrides these.\n"
    'role = "all"\n'
    "\n"
    "# Service endpoints (defaults shown; uncomment to override — XORCISE_* env still wins):\n"
    f'# host = "{HOST}"\n'
    f"# rest_port = {REST_PORT}\n"
    f"# otlp_port = {OTLP_PORT}\n"
    f"# runner_port = {RUNNER_PORT}\n"
    f"# headscale_port = {HEADSCALE_PORT}\n"
)
_DEFAULT_ENV = "# XORCISE secrets (0600). Set your BYOM model key:\n# XORCISE_MODEL_KEY=\n"


def _write_private(path: Path, text: str) -> None:
    """Write `text` to `path` so that it is never readable by anyone but the owner.

    `.env` holds the model API keys. Writing it with write_text and then calling chmod(0o600)
    leaves a window, under the usual 022 umask, in which a new file holding the key is 0644 and
    any local user can read it. Here a new file is created 0600 (a umask can only remove bits),
    and an existing one is tightened to 0600 before the key is written into it.

    The write stays in place, as write_text's was: the file keeps its inode, owner and hard links,
    a symlinked or bind-mounted `.env` is written through, and no temp file holding the key can be
    left behind."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)  # an existing file keeps its old mode through O_CREAT
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "w") as f:
        f.write(text)


def scaffold_config(home: Path) -> None:
    """Write a default config.toml + a 0600 .env template on first run; never overwrite."""
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.toml"
    if not cfg.exists():
        cfg.write_text(_DEFAULT_CONFIG_TOML)
    env = home / ".env"
    if not env.exists():
        _write_private(env, _DEFAULT_ENV)


def has_line_break(value: str) -> bool:
    """True when `value` would not survive as one ``KEY=value`` line of ``.env``.

    The test is the one the writer below reads the file back with, ``str.splitlines()``, not
    a search for ``\\n``/``\\r``: splitlines also breaks on ``\\v``, ``\\f``, ``\\x1c``-``\\x1e``,
    ``\\x85``, ``\\u2028`` and ``\\u2029``, and any of those splits the line just as surely. The
    REST setters and the CLI's piped-key check refuse a value on this same test (#160)."""
    return "".join(value.splitlines()) != value  # splitlines drops every separator it splits on


def set_env_vars(home: Path, values: dict[str, str | None]) -> None:
    """Upsert ``KEY=value`` lines in ``<home>/.env``, preserving every other line.

    The single guided writer for the local config surface: the GUI/CLI judge-model
    setters land here. A ``None``/empty value removes the key. Comments and unrelated vars are
    left untouched; the file is (re)written at 0600, and is never readable by others even
    briefly (see _write_private). Creates the file if absent.

    Raises ValueError, before anything is read or written, for a value with a line break in it:
    values are written unquoted, so the break would split the line, dotenv would read back only
    the first fragment, and the rest would persist as a line this writer cannot parse and so
    preserves on every later write (#160). The message names the variable, never the value — it
    may be a secret.
    """
    for name, value in values.items():
        if value and has_line_break(value):
            raise ValueError(f"{name} must be a single line — it contains a line break")
    home.mkdir(parents=True, exist_ok=True)
    env = home / ".env"
    lines = env.read_text().splitlines() if env.exists() else []

    def _key_of(line: str) -> str | None:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            return None
        return stripped.split("=", 1)[0].strip()

    remaining = {k: v for k, v in values.items()}
    out: list[str] = []
    for line in lines:
        key = _key_of(line)
        if key is not None and key in remaining:
            new = remaining.pop(key)
            if new:  # replace in place; drop the line when removing
                out.append(f"{key}={new}")
            continue
        out.append(line)
    # append any keys that weren't already present (skip removals of absent keys)
    for key, new in remaining.items():
        if new:
            out.append(f"{key}={new}")

    _write_private(env, "\n".join(out) + ("\n" if out else ""))
