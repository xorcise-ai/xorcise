"""Cache + serve the version-pinned tailscale client tarball (domain module). Does I/O.

The agent's sandbox may be air-gapped (no public egress); the server is not. This caches the
pinned static client tarball on the server — downloading it once per arch from pkgs.tailscale.com
— so run-control can serve it to the agent over the authed tailnet-join call. The agent then
needs no public internet to install tailscale. If the server itself can't fetch, the caller
surfaces the failure and the served join script falls back to pkgs.tailscale.com directly.
"""

from __future__ import annotations

import os
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from xorcise.core.runs.join import TAILSCALE_CLIENT_VERSION

# Whitelisted so the arch (which flows in from an agent-supplied query param) can never inject
# into the download URL — only these two map to a real, pinned pkgs.tailscale.com artifact.
SUPPORTED_ARCHES = ("amd64", "arm64")


class TailscaleDistError(RuntimeError):
    """The pinned client tarball could not be cached (bad arch or the server download failed)."""


def tarball_name(version: str, arch: str) -> str:
    return f"tailscale_{version}_{arch}.tgz"


def _pkgs_url(version: str, arch: str) -> str:
    return f"https://pkgs.tailscale.com/stable/{tarball_name(version, arch)}"


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (fixed https host, pinned path)
        dest.write_bytes(resp.read())


def ensure_tarball(
    cache_dir: Path,
    arch: str,
    *,
    version: str = "",
    fetch: Callable[[str, Path], None] | None = None,
) -> Path:
    """Return the cached pinned tarball for *arch*, downloading it once if absent.

    *arch* must be one of SUPPORTED_ARCHES (guards the download URL). Downloads to a temp file and
    renames atomically so a failed/partial fetch never leaves a file a later call would trust as
    complete. *fetch* is an injection seam for tests. Raises TailscaleDistError on a bad arch or a
    failed download.
    """
    v = version or TAILSCALE_CLIENT_VERSION
    if arch not in SUPPORTED_ARCHES:
        raise TailscaleDistError(f"unsupported arch {arch!r} (want one of {SUPPORTED_ARCHES})")
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / tarball_name(v, arch)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    # Download to a UNIQUE temp then atomically publish: concurrent cold-cache requests for the same
    # arch (sync endpoints run in a threadpool) must not share a temp and corrupt each other — last
    # complete writer wins. mkstemp gives a distinct path per call.
    fd, tmp_name = tempfile.mkstemp(dir=cache_dir, prefix=dest.name + ".", suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    do_fetch = fetch or _download
    try:
        do_fetch(_pkgs_url(v, arch), tmp)
    except Exception as e:  # network, disk, anything — surface uniformly; leave no partial file
        tmp.unlink(missing_ok=True)
        raise TailscaleDistError(f"failed to fetch the tailscale client for {arch}: {e}") from e
    tmp.replace(dest)  # atomic publish
    return dest
