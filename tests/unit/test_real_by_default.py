"""Real-by-default driver selection (role + use_stubs)."""

from __future__ import annotations

import shutil

import pytest

from xorcise.core.config import Settings
from xorcise.core.rest.mission_pull import _use_real_docker
from xorcise.core.runner.docker import StubDockerDriver


def _s(**kw) -> Settings:
    # init kwargs are highest-priority (init > env > toml), so these win regardless of host env.
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "role,use_stubs,expected",
    [
        ("all", False, True),
        ("runner", False, True),
        ("control", False, False),
        ("all", True, False),
        ("runner", True, False),
    ],
)
def test_use_real_docker_truth_table(role: str, use_stubs: bool, expected: bool) -> None:
    assert _use_real_docker(_s(role=role, use_stubs=use_stubs)) is expected


def test_build_pull_deps_real_when_role_all(monkeypatch) -> None:
    import xorcise.core.rest.mission_pull as cp

    sentinel = StubDockerDriver()
    monkeypatch.setattr(cp, "_real_docker_driver", lambda: sentinel)
    deps = cp.build_pull_deps(_s(role="all", use_stubs=False, missions_root="/tmp/x"))
    assert deps.driver is sentinel  # real path selected


def test_build_pull_deps_stub_when_use_stubs(monkeypatch) -> None:
    import xorcise.core.rest.mission_pull as cp

    def _boom() -> object:
        raise AssertionError("should not build real")

    monkeypatch.setattr(cp, "_real_docker_driver", _boom)
    deps = cp.build_pull_deps(_s(role="all", use_stubs=True, missions_root="/tmp/x"))
    assert isinstance(deps.driver, StubDockerDriver)


def test_explicit_use_docker_false_overrides() -> None:
    import xorcise.core.rest.mission_pull as cp

    deps = cp.build_pull_deps(
        _s(role="all", use_stubs=False, missions_root="/tmp/x"), use_docker=False
    )
    assert isinstance(deps.driver, StubDockerDriver)


def test_real_docker_driver_failloud_no_extra(monkeypatch) -> None:
    # the docker SDK is a BASE dependency. The driver MODULE imports fine without
    # it (the SDK import is lazy in __init__), so the absence must be detected by probing the SDK
    # itself — not by the driver-module import. Simulate the SDK being unimportable.
    import importlib.util

    import xorcise.core.rest.mission_pull as cp

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "docker" else real_find_spec(name, *a, **k),
    )
    with pytest.raises(RuntimeError, match="install is incomplete"):
        cp._real_docker_driver()


def test_real_docker_driver_failloud_daemon_down(monkeypatch) -> None:
    import importlib.util

    import xorcise.core.rest.mission_pull as cp
    import xorcise.core.runner.docker.driver as drv

    # Force the SDK-present branch so this exercises the daemon/construction failure even where
    # the `runner` extra is NOT installed (e.g. CI on xorcise[dev]) — otherwise the find_spec
    # probe short-circuits to the runner-extra message and this assertion never reaches "daemon".
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: object() if name == "docker" else real_find_spec(name, *a, **k),
    )

    class _Boom:
        def __init__(self) -> None:
            raise RuntimeError("connection refused")

    monkeypatch.setattr(drv, "DockerSdkDriver", _Boom)
    with pytest.raises(RuntimeError, match="daemon"):
        cp._real_docker_driver()


def test_real_docker_driver_threads_the_platform_setting(monkeypatch) -> None:
    # the factory must construct DockerSdkDriver with settings.docker_platform, so the
    # arm64-Mac default (linux/amd64) and any XORCISE_DOCKER_PLATFORM override reach pull/run.
    import importlib.util

    import xorcise.core.rest.mission_pull as cp
    import xorcise.core.runner.docker.driver as drv

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: object() if name == "docker" else real_find_spec(name, *a, **k),
    )
    captured: dict[str, object] = {}

    class _Capture:
        def __init__(self, *, platform: str) -> None:
            captured["platform"] = platform

    monkeypatch.setattr(drv, "DockerSdkDriver", _Capture)
    monkeypatch.setattr(
        "xorcise.core.config.get_settings", lambda: _s(docker_platform="linux/probe")
    )
    cp._real_docker_driver()
    assert captured["platform"] == "linux/probe"


def test_probe_headscale_remediation_when_unreachable() -> None:
    # an unreachable control plane must yield a remediation (like the Docker path),
    # not a bare HeadscaleError surfacing deep in create_run.
    import xorcise.core.rest.run_create as rc
    from xorcise.core.headscale import HeadscaleError, StubHeadscaleCli

    class _Down(StubHeadscaleCli):
        def version(self) -> str:
            raise HeadscaleError("No such container: headscale")

    with pytest.raises(RuntimeError, match="not reachable") as ei:
        rc._probe_headscale(_Down(), "headscale")
    assert "XORCISE_USE_STUBS" in str(ei.value)


def test_probe_headscale_ok_when_reachable() -> None:
    import xorcise.core.rest.run_create as rc
    from xorcise.core.headscale import StubHeadscaleCli

    class _Up(StubHeadscaleCli):
        def version(self) -> str:
            return "headscale v0.29.1"

    rc._probe_headscale(_Up(), "headscale")  # reachable → no raise


@pytest.mark.parametrize(
    "role,use_stubs,expected",
    [
        ("all", False, True),
        ("runner", False, True),
        ("control", False, False),
        ("all", True, False),
    ],
)
def test_use_real_headscale_truth_table(role: str, use_stubs: bool, expected: bool) -> None:
    from xorcise.core.rest.run_create import _use_real_headscale

    assert _use_real_headscale(_s(role=role, use_stubs=use_stubs)) is expected


def test_run_create_deps_real_fence_when_role_all(monkeypatch, migrated_home) -> None:
    import xorcise.core.rest.mission_pull as cp
    import xorcise.core.rest.run_create as rc
    from xorcise.core.config import get_settings
    from xorcise.core.headscale import StubHeadscaleCli

    monkeypatch.setattr(cp, "_real_docker_driver", lambda: StubDockerDriver())
    used: list[int] = []

    def _spy(s: object) -> StubHeadscaleCli:
        used.append(1)
        return StubHeadscaleCli()

    monkeypatch.setattr(rc, "_real_headscale_cli", _spy)
    monkeypatch.delenv("XORCISE_USE_STUBS", raising=False)
    monkeypatch.setenv("XORCISE_ROLE", "all")
    get_settings.cache_clear()
    rc.build_run_create_deps(get_settings())
    assert used  # the real headscale seam was used


def test_run_create_deps_stub_fence_when_use_stubs(monkeypatch, migrated_home) -> None:
    import xorcise.core.rest.run_create as rc
    from xorcise.core.config import get_settings
    from xorcise.core.headscale import StubHeadscaleCli

    used: list[int] = []

    def _spy(s: object) -> StubHeadscaleCli:
        used.append(1)
        return StubHeadscaleCli()

    monkeypatch.setattr(rc, "_real_headscale_cli", _spy)
    deps = rc.build_run_create_deps(get_settings())  # conftest forces use_stubs
    assert not used and deps.fence is not None


def test_airgapped_settings_flow_into_deps(monkeypatch, migrated_home, tmp_path) -> None:
    # headscale_url overrides the login server; the CA file content + host alias are
    # read into the deps so create_run can hand them to the router. (Docker-free — stub path.)
    import xorcise.core.rest.run_create as rc
    from xorcise.core.config import get_settings

    ca = tmp_path / "ca.pem"
    ca.write_text("PEM-CA-DATA")
    monkeypatch.setenv("XORCISE_HEADSCALE_URL", "https://headscale.local:8443")
    monkeypatch.setenv("XORCISE_HEADSCALE_CA_CERT", str(ca))
    monkeypatch.setenv("XORCISE_HEADSCALE_HOST_ALIAS", "headscale.local:172.17.0.1")
    get_settings.cache_clear()
    deps = rc.build_run_create_deps(get_settings())  # conftest forces use_stubs
    assert deps.login_server == "https://headscale.local:8443"
    assert deps.ca_cert == "PEM-CA-DATA"
    assert deps.extra_hosts == ("headscale.local:172.17.0.1",)


def test_advertise_host_shapes_plain_login_server(monkeypatch, migrated_home) -> None:
    # No TLS url: the tailnet login server (router + agent join) dials headscale_advertise_host,
    # not the loopback REST bind. This is topology-independent — the tailnet control
    # plane never uses the host.docker.internal alias.
    import xorcise.core.rest.run_create as rc
    from xorcise.core.config import get_settings

    monkeypatch.delenv("XORCISE_HEADSCALE_URL", raising=False)
    monkeypatch.setenv("XORCISE_HEADSCALE_ADVERTISE_HOST", "172.17.0.1")
    get_settings.cache_clear()
    assert rc.build_run_create_deps(get_settings()).login_server == "http://172.17.0.1:8080"


def test_config_toml_persists_headscale_wiring(monkeypatch, migrated_home) -> None:
    # `xorcise up` writes these into ~/.xorcise/config.toml; get_settings must pick
    # them up with NO env exports (the "just run xorcise up" UX).
    from pathlib import Path

    import xorcise.core.rest.run_create as rc
    from xorcise.core.config import get_settings

    (Path(migrated_home) / "config.toml").write_text(
        'headscale_url = "https://headscale.local:8443"\nheadscale_advertise_host = "172.17.0.1"\n'
    )
    for v in ("XORCISE_HEADSCALE_URL", "XORCISE_HEADSCALE_ADVERTISE_HOST"):
        monkeypatch.delenv(v, raising=False)
    get_settings.cache_clear()
    assert rc.build_run_create_deps(get_settings()).login_server == "https://headscale.local:8443"


def test_run_create_deps_real_control_when_role_all(monkeypatch, migrated_home) -> None:
    import xorcise.core.rest.mission_pull as cp
    import xorcise.core.rest.run_create as rc
    from xorcise.core.config import get_settings
    from xorcise.core.headscale import StubHeadscaleCli
    from xorcise.core.orchestration.clients.control import InProcessControlStub

    monkeypatch.setattr(cp, "_real_docker_driver", lambda: StubDockerDriver())
    # Stub the headscale seam so the live reachability probe doesn't fire — this test
    # asserts the CONTROL adapter is real, not that a control plane is running.
    monkeypatch.setattr(rc, "_real_headscale_cli", lambda s: StubHeadscaleCli())
    monkeypatch.delenv("XORCISE_USE_STUBS", raising=False)  # conftest sets it; here we want real
    monkeypatch.setenv("XORCISE_ROLE", "all")
    get_settings.cache_clear()
    deps = rc.build_run_create_deps(get_settings())
    assert not isinstance(deps.control, InProcessControlStub)  # real control adapter


def test_run_create_deps_stub_control_when_use_stubs(migrated_home) -> None:
    # conftest forces XORCISE_USE_STUBS=1 → stub control even on role all
    import xorcise.core.rest.run_create as rc
    from xorcise.core.config import get_settings
    from xorcise.core.orchestration.clients.control import InProcessControlStub

    deps = rc.build_run_create_deps(get_settings())
    assert isinstance(deps.control, InProcessControlStub)


def test_build_bundle_builder_real_when_role_all(monkeypatch) -> None:
    import xorcise.core.rest.ingest as ing
    from xorcise.core.missions import StubBundleBuilder

    sentinel = StubBundleBuilder()
    monkeypatch.setattr(ing, "_real_bundle_builder", lambda: sentinel)
    assert ing.build_bundle_builder(_s(role="all", use_stubs=False)) is sentinel


def test_build_bundle_builder_stub_when_use_stubs() -> None:
    import xorcise.core.rest.ingest as ing
    from xorcise.core.missions import StubBundleBuilder

    assert isinstance(ing.build_bundle_builder(_s(role="all", use_stubs=True)), StubBundleBuilder)


def test_real_bundle_builder_failloud_no_extra(monkeypatch) -> None:
    import sys

    import xorcise.core.rest.ingest as ing

    monkeypatch.setitem(sys.modules, "xorcise.core.runner.docker.build", None)
    # A local build shells out to docker, so a missing BINARY is the real blocker;
    # an unimportable build module means the install itself is broken. Neither is
    # fixed by a pip extra (`runner` is an empty no-op).
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    with pytest.raises(RuntimeError, match="install is incomplete"):
        ing._real_bundle_builder()
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="Docker CLI"):
        ing._real_bundle_builder()


def test_run_create_deps_all_three_real_on_role_all(monkeypatch, migrated_home) -> None:
    # composition guard: control + fence + pull driver all go real together on role all.
    import xorcise.core.rest.mission_pull as cp
    import xorcise.core.rest.run_create as rc
    from xorcise.core.config import get_settings
    from xorcise.core.headscale import StubHeadscaleCli
    from xorcise.core.orchestration.clients.control import InProcessControlStub

    driver = StubDockerDriver()
    monkeypatch.setattr(cp, "_real_docker_driver", lambda: driver)
    used: list[int] = []

    def _spy(s: object) -> StubHeadscaleCli:
        used.append(1)
        return StubHeadscaleCli()

    monkeypatch.setattr(rc, "_real_headscale_cli", _spy)
    monkeypatch.delenv("XORCISE_USE_STUBS", raising=False)
    monkeypatch.setenv("XORCISE_ROLE", "all")
    get_settings.cache_clear()

    deps = rc.build_run_create_deps(get_settings())
    assert not isinstance(deps.control, InProcessControlStub)  # real control
    assert used  # real fence seam used
    assert deps.pull.driver is driver  # real pull driver — all three compose
