import pytest

from xorcise.core.runs.join import TAILSCALE_CLIENT_VERSION
from xorcise.core.runs.tailscale_dist import (
    SUPPORTED_ARCHES,
    TailscaleDistError,
    ensure_tarball,
    tarball_name,
)

pytestmark = pytest.mark.unit


def test_tarball_name_is_the_pinned_pkgs_layout():
    assert tarball_name(TAILSCALE_CLIENT_VERSION, "amd64") == (
        f"tailscale_{TAILSCALE_CLIENT_VERSION}_amd64.tgz"
    )


def test_downloads_once_then_serves_from_cache(tmp_path):
    calls: list[str] = []

    def fake_fetch(url: str, dest) -> None:
        calls.append(url)
        dest.write_bytes(b"TGZBYTES")

    p1 = ensure_tarball(tmp_path, "amd64", fetch=fake_fetch)
    assert p1.read_bytes() == b"TGZBYTES"
    assert p1.name == tarball_name(TAILSCALE_CLIENT_VERSION, "amd64")
    # second call hits the cache — no second download
    p2 = ensure_tarball(tmp_path, "amd64", fetch=fake_fetch)
    assert p2 == p1
    assert len(calls) == 1  # downloaded exactly once
    assert "pkgs.tailscale.com" in calls[0]
    assert TAILSCALE_CLIENT_VERSION in calls[0]


def test_rejects_unsupported_arch_before_downloading(tmp_path):
    called = False

    def fake_fetch(url: str, dest) -> None:
        nonlocal called
        called = True

    with pytest.raises(TailscaleDistError):
        ensure_tarball(tmp_path, "mips; rm -rf /", fetch=fake_fetch)
    assert called is False  # never reached the download with an unvalidated arch
    assert set(SUPPORTED_ARCHES) == {"amd64", "arm64"}


def test_download_failure_surfaces_as_dist_error(tmp_path):
    def boom(url: str, dest) -> None:
        raise OSError("no route to host")

    with pytest.raises(TailscaleDistError):
        ensure_tarball(tmp_path, "arm64", fetch=boom)
    # a failed download must leave NO residue (no cached tarball, no leftover temp part file)
    assert list(tmp_path.iterdir()) == []


def test_concurrent_downloads_use_distinct_temp_files(tmp_path):
    # Two cold-cache downloads for the same arch must not share a temp path (else interleaved writes
    # corrupt the tarball). Capture the temp path each fetch is handed; they must differ.
    seen: list[str] = []

    def fake_fetch(url: str, dest) -> None:
        seen.append(str(dest))
        dest.write_bytes(b"TGZ")

    ensure_tarball(tmp_path, "amd64", fetch=fake_fetch)
    (tmp_path / tarball_name(TAILSCALE_CLIENT_VERSION, "amd64")).unlink()  # force a second download
    ensure_tarball(tmp_path, "amd64", fetch=fake_fetch)
    assert len(seen) == 2 and seen[0] != seen[1]  # distinct temp paths
