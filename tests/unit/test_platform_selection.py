# tests/unit/test_platform_selection.py
"""Native-first platform selection (contract §16/AS1–AS5, §43-CL1/CL2/CL6/CL8).

The pull spine decides the execution platform BEFORE any byte moves: operator override
first, host-native when the mission validated it, AMD64 under emulation as the fallback,
and a typed refusal when no execution path exists. After the pull, the landed architecture
is verified against the selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from xorcise.core.catalog import StubCatalogSource
from xorcise.core.catalog.source import MissionDetail, PlatformImage
from xorcise.core.contracts.errors import PlatformUnsupportedError, PullError
from xorcise.core.missions import get_installed
from xorcise.core.rest.mission_pull import PullDeps, _select_platform, pull_mission
from xorcise.core.runner.docker import StubDockerDriver

pytestmark = pytest.mark.unit

_BOTH = ("linux/amd64", "linux/arm64")


# ── the decision table (pure) ────────────────────────────────────────────────────────────────


def test_override_wins_unconditionally() -> None:
    assert _select_platform(_BOTH, "linux/arm64", "linux/ppc64le") == ("linux/ppc64le", None)


def test_no_offered_list_makes_no_selection() -> None:
    # A pre-contract catalog advertises nothing: the driver's construction-time behaviour,
    # exactly as before this feature.
    assert _select_platform((), "linux/arm64", "") == (None, None)


def test_native_preferred_when_offered() -> None:
    assert _select_platform(_BOTH, "linux/arm64", "") == ("linux/arm64", None)
    assert _select_platform(_BOTH, "linux/amd64", "") == ("linux/amd64", None)


def test_amd64_fallback_carries_the_notice() -> None:
    selected, notice = _select_platform(("linux/amd64",), "linux/arm64", "")
    assert selected == "linux/amd64"
    assert notice is not None
    assert "Native ARM64 image unavailable" in notice
    assert "emulation" in notice


def test_unknown_host_still_selects_from_the_offer() -> None:
    selected, notice = _select_platform(("linux/amd64",), None, "")
    assert selected == "linux/amd64"
    assert notice is None  # nothing to warn about: we cannot claim the host is non-native


def test_no_execution_path_raises_typed(capsys) -> None:
    with pytest.raises(PlatformUnsupportedError) as exc:
        _select_platform(("linux/arm64",), "linux/amd64", "")
    assert "linux/arm64" in str(exc.value)  # names what the mission does support
    assert isinstance(exc.value, PullError)  # rides the existing pull-error plumbing


# ── the spine wires the decision to the driver (AS5) and verifies it (§43-CL6) ───────────────


class _Source(StubCatalogSource):
    platforms: tuple[str, ...] = _BOTH

    def fetch_detail(self, mission_id: str) -> MissionDetail:
        return MissionDetail(
            manifest=self.fetch_manifest(mission_id),
            mission_version="1.0.0",
            index_digest="sha256:idx",
            platforms=tuple(
                PlatformImage(
                    os=p.split("/")[0], architecture=p.split("/")[1], digest=f"sha256:{p}"
                )
                for p in self.platforms
            ),
        )


class _ArmHost(StubDockerDriver):
    def daemon_platform(self) -> str | None:
        return "linux/arm64"

    def image_platform(self, image: str) -> str | None:
        return self.pulled_platform  # the registry served exactly what was asked


def test_pull_passes_the_native_selection_to_docker(tmp_path: Path) -> None:
    driver = _ArmHost()
    ic = pull_mission(
        "sqli-login", PullDeps(source=_Source(enabled=True), driver=driver, install_root=tmp_path)
    )
    assert driver.pulled_platform == "linux/arm64"  # explicit, native-first
    assert ic.platform == "linux/arm64"  # and the install record agrees (§30)
    assert ic.platform_digest == "sha256:linux/arm64"


def test_pull_override_reaches_docker(tmp_path: Path) -> None:
    driver = _ArmHost()
    deps = PullDeps(
        source=_Source(enabled=True),
        driver=driver,
        install_root=tmp_path,
        platform_override="linux/amd64",
    )
    pull_mission("sqli-login", deps)
    assert driver.pulled_platform == "linux/amd64"


def test_pull_refuses_before_downloading_when_no_path_exists(tmp_path: Path) -> None:
    class _ArmOnly(_Source):
        platforms = ("linux/arm64",)

    class _Amd(StubDockerDriver):
        def daemon_platform(self) -> str | None:
            return "linux/amd64"

    driver = _Amd()
    with pytest.raises(PlatformUnsupportedError):
        pull_mission(
            "sqli-login",
            PullDeps(source=_ArmOnly(enabled=True), driver=driver, install_root=tmp_path),
        )
    assert driver.pulled == []  # refused BEFORE any byte moved (CG5 analogue)
    assert get_installed("sqli-login", tmp_path) is None


def test_pull_verifies_the_landed_architecture(tmp_path: Path) -> None:
    class _LyingRegistry(_ArmHost):
        def image_platform(self, image: str) -> str | None:
            return "linux/amd64"  # asked for arm64, got amd64

    with pytest.raises(PullError, match="resolved to linux/amd64"):
        pull_mission(
            "sqli-login",
            PullDeps(source=_Source(enabled=True), driver=_LyingRegistry(), install_root=tmp_path),
        )
    assert get_installed("sqli-login", tmp_path) is None  # nothing installed on mismatch


def test_pre_contract_pull_is_untouched(tmp_path: Path) -> None:
    # No platforms offered ⇒ no selection, no platform kwarg, no verification — byte-for-byte
    # the pre-feature behaviour against prod.
    driver = StubDockerDriver()
    pull_mission(
        "sqli-login",
        PullDeps(source=StubCatalogSource(enabled=True), driver=driver, install_root=tmp_path),
    )
    assert driver.pulled_platform is None
