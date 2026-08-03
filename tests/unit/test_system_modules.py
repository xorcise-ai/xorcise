"""Module health grouped by service role (GET /api/system → planes).

The operator surfaces (status bar, Settings Modules card) render XORCISE as the four roles it
is actually built from. These tests pin the two properties that make that honest:

  1. a module the active role does NOT run reports `not_deployed` — absent, never broken;
  2. the expensive probes are memoised, because a status bar on every page polls this view.
"""

from __future__ import annotations

import pytest

from xorcise.core.config import Settings, get_settings
from xorcise.core.contracts.config import PlaneStatus
from xorcise.core.rest import system_view

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_cache():
    system_view.reset_probe_cache()
    yield
    system_view.reset_probe_cache()


def _settings(**overrides: object) -> Settings:
    return get_settings().model_copy(update=overrides)


def _by_name(planes: tuple[PlaneStatus, ...]) -> dict[str, PlaneStatus]:
    return {p.name: p for p in planes}


def test_every_module_is_tagged_with_its_owning_role_and_a_human_label(migrated_home) -> None:
    planes = _by_name(system_view.build_system_info(_settings(role="all")).planes)
    assert {p.name: p.role for p in planes.values()} == {
        "rest": "control",
        "docker": "runner",
        "headscale": "headscale",
        "otlp": "collector",
    }
    # Labels are the words the CLI already uses for these planes (cli/_ux.py::_SERVICE_LABELS),
    # so the two surfaces never invent competing names for one thing.
    assert planes["rest"].label == "REST API"
    assert planes["otlp"].label == "OTLP receiver"
    assert planes["docker"].label == "Docker"


def test_modules_the_role_does_not_run_are_not_deployed_rather_than_down(migrated_home) -> None:
    """A control-only host runs no runner, tailnet or collector. Reporting those as `down`
    would paint a correctly-configured deployment red — the failure mode this state exists for."""
    planes = _by_name(system_view.build_system_info(_settings(role="control")).planes)
    for name in ("docker", "headscale", "otlp"):
        assert planes[name].state == "not_deployed", name
        assert planes[name].detail == "not on this host"
    # ...and the modules it DOES run are still probed for real.
    assert planes["rest"].state in {"ok", "down"}


def test_not_deployed_modules_are_never_probed(migrated_home, monkeypatch) -> None:
    """Skipping the probe is the point: probing a module that lives on another machine both
    lies about the result and pays for the privilege."""
    calls: list[str] = []

    def spy_docker(_settings: Settings) -> PlaneStatus:
        calls.append("docker")
        return system_view._not_deployed("docker")

    monkeypatch.setattr(system_view, "_docker_plane", spy_docker)
    system_view.build_system_info(_settings(role="control"))
    assert calls == []


def test_ok_always_agrees_with_state(migrated_home) -> None:
    # `ok` predates `state` and is still read by the dashboard/checklist counters. If the two
    # ever disagree, one surface says healthy while another says broken.
    for role in ("all", "control"):
        for plane in system_view.build_system_info(_settings(role=role)).planes:
            assert plane.ok == (plane.state == "ok"), plane


def test_stub_mode_reports_headscale_absent_not_broken(migrated_home) -> None:
    # `--stub` deliberately runs without a tailnet, so there is no container to find. A red
    # module on every stub demo would be a false alarm about a dependency that install lacks.
    planes = _by_name(system_view.build_system_info(_settings(role="all", use_stubs=True)).planes)
    assert planes["headscale"].state == "not_deployed"
    assert "stub" in planes["headscale"].detail


def test_expensive_probes_are_memoised(migrated_home, monkeypatch) -> None:
    """The status bar polls this view from every page; without memoisation each poll would pay
    for two docker subprocesses and a REMOTE catalog round-trip (measured ~0.67s on its own)."""
    docker_calls = 0

    def counting_docker(_settings: Settings) -> PlaneStatus:
        nonlocal docker_calls
        docker_calls += 1
        return system_view._plane("docker", ok=True, detail="ok", location="local daemon")

    monkeypatch.setattr(system_view, "_docker_plane", counting_docker)
    settings = _settings(role="all")
    system_view.build_system_info(settings)
    system_view.build_system_info(settings)
    system_view.build_system_info(settings)
    assert docker_calls == 1

    system_view.reset_probe_cache()
    system_view.build_system_info(settings)
    assert docker_calls == 2


def test_cache_ttl_exceeds_the_gui_poll_interval() -> None:
    """A TTL shorter than the client's poll makes every poll a miss — the cache would then cost
    complexity and buy nothing. The GUI polls /system every 15s (features/settings/queries.ts)."""
    gui_poll_seconds = 15.0
    assert gui_poll_seconds < system_view._DEFAULT_TTL
    for key, ttl in system_view._TTL_SECONDS.items():
        assert gui_poll_seconds < ttl, key


def test_catalog_switch_drops_the_memoised_catalog_probe(migrated_home, tmp_path) -> None:
    """The catalog probe is memoised for minutes because it is a remote round-trip. The connect
    switch takes effect immediately, so a stale memo would keep reporting the OLD state exactly
    when the operator is watching for it to change."""
    import time

    from xorcise.core.rest.config_view import apply_catalog_update

    system_view._probe_cache["catalog"] = (time.monotonic(), "stale-sentinel")
    apply_catalog_update(tmp_path, connected=False)
    assert "catalog" not in system_view._probe_cache


def test_stub_mode_reports_docker_absent_not_broken(migrated_home) -> None:
    """`--stub` is the documented no-Docker path (README: "No Docker on the box? xorcise up
    --stub"), and `_use_real_docker` gates on the same flag, so runs never touch a daemon there.
    Probing for one anyway turned the whole Runner role red on a correctly-configured install —
    the identical false alarm the headscale probe already avoids."""
    planes = _by_name(system_view.build_system_info(_settings(role="all", use_stubs=True)).planes)
    assert planes["docker"].state == "not_deployed"
    assert "stub" in planes["docker"].detail


def test_external_headscale_is_probed_over_http_not_by_container(
    migrated_home, monkeypatch
) -> None:
    """A host pointed at a control plane on ANOTHER machine has no local container by design.
    Probing for one reports a correctly-configured remote deployment as permanently broken —
    which is what an operator hits the moment they edit the Headscale URL to another host."""
    execs: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> None:
        execs.append(cmd)
        raise AssertionError("must not exec a container for an external control plane")

    class _Resp:
        status_code = 200

    monkeypatch.setattr("xorcise.core.rest.system_view.subprocess.run", fake_run)
    monkeypatch.setattr("xorcise.core.rest.system_view.httpx.get", lambda *a, **kw: _Resp())
    monkeypatch.setattr(system_view, "_managed_headscale_url", lambda: "https://172.17.0.1:443")
    # conftest forces XORCISE_USE_STUBS=1 for hermeticity; this test is about the real path.
    plane = system_view._headscale_plane(
        _settings(headscale_url="https://hs.remote.example:8080", use_stubs=False)
    )

    assert plane.state == "ok"
    assert plane.location == "https://hs.remote.example:8080"  # addressed by the login server
    assert execs == []


def test_locally_provisioned_headscale_is_probed_by_container(migrated_home, monkeypatch) -> None:
    """When the URL is the one OUR OWN provisioning wrote, the container is still the real
    dependency: run creation execs into it whatever the URL says, so a URL that answers can sit
    alongside every run 503-ing (cli/_diagnostics.py::control_plane documents this)."""
    managed = "https://172.17.0.1:443"
    execs: list[list[str]] = []

    class _Done:
        returncode = 0

    def fake_run(cmd: list[str], **kwargs: object) -> _Done:
        execs.append(cmd)
        return _Done()

    # Pinned so the branch is exercised on a CI box that has no docker binary.
    monkeypatch.setattr("xorcise.core.rest.system_view.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("xorcise.core.rest.system_view.subprocess.run", fake_run)
    monkeypatch.setattr(system_view, "_managed_headscale_url", lambda: managed)
    plane = system_view._headscale_plane(_settings(headscale_url=managed, use_stubs=False))

    assert plane.state == "ok"
    assert plane.location == managed  # shown by address, not by container name
    assert execs and execs[0][:2] == ["docker", "exec"]
