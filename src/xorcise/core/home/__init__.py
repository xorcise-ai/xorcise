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


def scaffold_config(home: Path) -> None:
    """Write a default config.toml + a 0600 .env template on first run; never overwrite."""
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.toml"
    if not cfg.exists():
        cfg.write_text(_DEFAULT_CONFIG_TOML)
    env = home / ".env"
    if not env.exists():
        env.write_text(_DEFAULT_ENV)
        env.chmod(0o600)


def set_env_vars(home: Path, values: dict[str, str | None]) -> None:
    """Upsert ``KEY=value`` lines in ``<home>/.env``, preserving every other line.

    The single guided writer for the local config surface: the GUI/CLI judge-model
    setters land here. A ``None``/empty value removes the key. Comments and unrelated vars are
    left untouched; the file is (re)written at 0600. Creates the file if absent.
    """
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

    env.write_text("\n".join(out) + ("\n" if out else ""))
    env.chmod(0o600)
