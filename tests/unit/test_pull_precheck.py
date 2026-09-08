"""The pull spine's nesting precheck: WHICH platform it is asked about, and WHEN.

Two behavioural facts at the centre of #80 that nothing else pins: the precheck receives the
platform `_select_platform` chose for this host, and it runs BEFORE `driver.pull` — a change that
moved the gate below the download would pass every other lane. Plus the one hole the review
found: a pre-contract entry (no platform list) makes no selection, so the gate is asked about the
native platform while the registry may land a single-arch FOREIGN image — the gate must be asked
again about what actually landed, before anything is installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xorcise.core.catalog import StubCatalogSource
from xorcise.core.catalog.source import MissionDetail, PlatformImage
from xorcise.core.contracts.errors import NestedContainersUnavailableError
from xorcise.core.rest.mission_pull import PullDeps, pull_mission
from xorcise.core.runner.docker import StubDockerDriver

pytestmark = pytest.mark.unit


class _MultiArchSource(StubCatalogSource):
    """A contract-era entry publishing amd64 AND arm64."""

    def fetch_detail(self, mission_id: str) -> MissionDetail:
        return MissionDetail(
            manifest=self.fetch_manifest(mission_id),
            mission_version="1.0.0",
            mission_base_version="2.0.0",
            content_hash="0" * 16,
            pull_ref="reg/xorcise/mis-sqli-login:latest",
            release_ref="reg/xorcise/mis-sqli-login:1.0.0-base2.0.0",
            index_digest="sha256:idx",
            platforms=(
                PlatformImage(os="linux", architecture="amd64", digest="sha256:amd"),
                PlatformImage(os="linux", architecture="arm64", digest="sha256:arm", variant="v8"),
            ),
            base_index_digest="sha256:base",
            base_platform_digests={"amd64": "sha256:bamd", "arm64": "sha256:barm"},
        )


class _RecordingDriver(StubDockerDriver):
    """An arm64 host whose registry serves what it is asked; records the ORDER of events."""

    def __init__(self, *, native: str = "linux/arm64", lands: str | None = None) -> None:
        super().__init__()
        self.events: list[str] = []
        self._native = native
        self._lands = lands  # what image_platform reports after the pull (None ⇒ the request)
        self._pulled_platform: str | None = None

    def daemon_platform(self) -> str | None:
        return self._native

    def image_exists(self, image: str) -> bool:
        return False  # a download ahead

    def pull(self, image: str, **kwargs: object) -> None:
        platform = kwargs.get("platform")
        self._pulled_platform = platform if isinstance(platform, str) else None
        self.events.append(f"pull:{self._pulled_platform}")

    def image_platform(self, image: str) -> str | None:
        return self._lands or self._pulled_platform or self._native


def test_precheck_gets_the_selected_platform_before_the_pull(tmp_path: Path) -> None:
    driver = _RecordingDriver()
    deps = PullDeps(source=_MultiArchSource(enabled=True), driver=driver, install_root=tmp_path)

    def precheck(platform: str | None) -> None:
        driver.events.append(f"precheck:{platform}")

    pull_mission("sqli-login", deps, precheck=precheck)
    # Asked about the platform selected for THIS host (native arm64), and asked before any byte.
    assert driver.events == ["precheck:linux/arm64", "pull:linux/arm64"]


def test_a_refusing_precheck_costs_no_download(tmp_path: Path) -> None:
    driver = _RecordingDriver()
    deps = PullDeps(source=_MultiArchSource(enabled=True), driver=driver, install_root=tmp_path)

    def refuse(platform: str | None) -> None:
        raise NestedContainersUnavailableError(f"cannot nest {platform}")

    with pytest.raises(NestedContainersUnavailableError):
        pull_mission("sqli-login", deps, precheck=refuse)
    assert driver.events == []  # refused before `pull` ran
    assert not (tmp_path / "sqli-login").exists()


def test_precheck_follows_the_operator_override(tmp_path: Path) -> None:
    driver = _RecordingDriver()
    deps = PullDeps(
        source=_MultiArchSource(enabled=True),
        driver=driver,
        install_root=tmp_path,
        platform_override="linux/amd64",
    )
    seen: list[str | None] = []
    pull_mission("sqli-login", deps, precheck=seen.append)
    assert seen == ["linux/amd64"]  # the override wins the selection, so it is what gets gated


def test_pre_contract_entry_is_regated_on_what_actually_landed(tmp_path: Path) -> None:
    """No platform list ⇒ no selection ⇒ the gate is asked about native. If the registry then
    serves a single-arch foreign image, the gate is asked AGAIN about that platform before the
    install — a refusal there leaves the mission cleanly not-installed instead of recording a
    platform run-create would refuse."""
    driver = _RecordingDriver(lands="linux/amd64")  # the registry only has amd64
    deps = PullDeps(source=StubCatalogSource(enabled=True), driver=driver, install_root=tmp_path)
    asked: list[str | None] = []

    def gate(platform: str | None) -> None:
        asked.append(platform)
        if platform == "linux/amd64":
            raise NestedContainersUnavailableError("cannot nest linux/amd64 here")

    with pytest.raises(NestedContainersUnavailableError):
        pull_mission("sqli-login", deps, precheck=gate)
    assert asked == [None, "linux/amd64"]  # native first (nothing better known), then the landing
    assert driver.events == ["pull:None"]
    assert not (tmp_path / "sqli-login").exists()


def test_pre_contract_entry_landing_native_is_not_regated(tmp_path: Path) -> None:
    driver = _RecordingDriver()  # the registry serves the host's own arch
    deps = PullDeps(source=StubCatalogSource(enabled=True), driver=driver, install_root=tmp_path)
    asked: list[str | None] = []
    pull_mission("sqli-login", deps, precheck=asked.append)
    assert asked == [None]
