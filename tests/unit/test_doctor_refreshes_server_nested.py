"""`doctor` clears the nested-container refusal the SERVER holds, not only its own (#195).

The verdict memo is per process, and run creation reads the server's copy. A re-probe in the
CLI's process used to refresh a memo that then exited with it, so a host fixed after a refusal
(Rosetta enabled on Apple Silicon) kept being refused until XORCISE restarted. The server now
re-probes on request, and `doctor` asks it whenever this home's server is running.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from xorcise.core.cli import _diagnostics as diag
from xorcise.core.config import Settings
from xorcise.core.contracts.errors import NestedContainersUnavailableError
from xorcise.core.home import pid_file
from xorcise.core.rest import docker_runtime as dr
from xorcise.core.runner.docker.rosetta import NestedSupport

pytestmark = pytest.mark.unit

AMD64 = "linux/amd64"
NATIVE = "linux/arm64"


@pytest.fixture(autouse=True)
def _clear_memo():
    dr.reset_nested_support_memo()
    yield
    dr.reset_nested_support_memo()


def _settings(*, stubs: bool = False, role: str = "all") -> Settings:
    return Settings(_env_file=None, use_stubs=stubs, role=role, nested_container_check="enforce")  # type: ignore[call-arg]


def _host(monkeypatch, healthy: dict[str, bool]) -> None:
    """A daemon whose native platform is arm64 and whose amd64 nesting is `healthy[AMD64]`,
    read at probe time, so a test can fix the host between two probes."""

    def _probe(_factory, key):
        ok = healthy.get(key or NATIVE, True)
        detail = "nested verified" if ok else "rosetta error: failed to open elf"
        return NestedSupport(ok, detail, remediation="" if ok else "enable Rosetta"), NATIVE

    monkeypatch.setattr(dr, "_probe", _probe)


def _server(monkeypatch, settings: Settings) -> TestClient:
    from xorcise.core.rest.routers import system
    from xorcise.core.roles.boot.role_all import build_rest_app

    monkeypatch.setattr(system, "get_settings", lambda: settings)
    return TestClient(build_rest_app())


# ------------------------------------------------------------------------------ server


def test_a_refresh_clears_the_refusal_the_next_run_reads(monkeypatch, migrated_home) -> None:
    settings = _settings()
    healthy = {AMD64: False}
    _host(monkeypatch, healthy)
    with pytest.raises(NestedContainersUnavailableError):
        dr.require_nested_support(settings, object, platform=AMD64)  # a run is refused

    healthy[AMD64] = True  # the operator enables Rosetta
    with pytest.raises(NestedContainersUnavailableError):
        dr.require_nested_support(settings, object, platform=AMD64)  # still cached

    r = _server(monkeypatch, settings).post("/api/system/nested-support/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["native"]["ok"] is True
    assert [(v["platform"], v["ok"]) for v in body["foreign"]] == [(AMD64, True)]
    dr.require_nested_support(settings, object, platform=AMD64)  # the next run proceeds


def test_a_refresh_reports_a_refusal_that_still_stands(monkeypatch, migrated_home) -> None:
    settings = _settings()
    _host(monkeypatch, {AMD64: False})
    with pytest.raises(NestedContainersUnavailableError):
        dr.require_nested_support(settings, object, platform=AMD64)
    foreign = (
        _server(monkeypatch, settings).post("/api/system/nested-support/refresh").json()["foreign"]
    )
    assert foreign == [
        {
            "platform": AMD64,
            "ok": False,
            "detail": "rosetta error: failed to open elf",
            "remediation": "enable Rosetta",
        }
    ]


def test_stub_mode_has_nothing_to_probe(monkeypatch, migrated_home) -> None:
    r = _server(monkeypatch, _settings(stubs=True)).post("/api/system/nested-support/refresh")
    assert r.status_code == 409


def test_a_role_that_deploys_no_runs_has_nothing_to_probe(monkeypatch, migrated_home) -> None:
    """Run creation only probes where it deploys (the runner roles), so only there does a
    re-probe clear anything; elsewhere it would start a privileged probe for nothing."""

    def _forbidden(_factory, _key):
        raise AssertionError("a non-runner role must not probe")

    monkeypatch.setattr(dr, "_probe", _forbidden)
    r = _server(monkeypatch, _settings(role="control")).post("/api/system/nested-support/refresh")
    assert r.status_code == 409


def test_a_refresh_does_not_stall_run_creation(monkeypatch) -> None:
    """The probes take minutes on a cold host. Run creation takes the memo lock even for a cached
    verdict, so the refresh must not hold that lock while it probes."""
    import threading

    settings = _settings()
    _host(monkeypatch, {AMD64: True})
    dr.require_nested_support(settings, object, platform=AMD64)  # cached, as after a first run

    probing, release = threading.Event(), threading.Event()
    quick = dr._probe

    def _slow(factory, key):
        probing.set()
        release.wait(5)
        return quick(factory, key)

    monkeypatch.setattr(dr, "_probe", _slow)
    refresh = threading.Thread(target=dr.refresh_nested_support, args=(settings, object))
    refresh.start()
    try:
        assert probing.wait(5)
        done = threading.Event()

        def _create() -> None:
            dr.require_nested_support(settings, object, platform=AMD64)
            done.set()

        create = threading.Thread(target=_create)
        create.start()
        assert done.wait(2), "a run create waited on the refresh's probe"
    finally:
        release.set()
        refresh.join(5)


def test_an_unreachable_daemon_is_a_503_not_a_500(monkeypatch, migrated_home) -> None:
    def _down(_factory, _key):
        raise RuntimeError("Docker daemon is not reachable — start Docker")

    monkeypatch.setattr(dr, "_probe", _down)
    r = _server(monkeypatch, _settings()).post("/api/system/nested-support/refresh")
    assert r.status_code == 503
    assert "not reachable" in r.json()["detail"]


# ------------------------------------------------------------------------------ doctor


def _local_probe_forbidden(monkeypatch) -> None:
    def _forbidden():
        raise AssertionError("doctor probed locally although the server answered")

    monkeypatch.setattr(diag, "nested_containers", _forbidden)
    monkeypatch.setattr(diag, "nested_containers_foreign", _forbidden)


def _local_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        diag, "nested_containers", lambda: diag.Check("nested containers", True, "local")
    )
    monkeypatch.setattr(diag, "nested_containers_foreign", lambda: [])


def _server_up() -> None:
    pid_file().parent.mkdir(parents=True, exist_ok=True)
    pid_file().write_text("12345")


def _answer(monkeypatch, status: int, body: object) -> list[str]:
    asked: list[str] = []

    def _post(url, **_kw):
        asked.append(url)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr("xorcise.core.cli._diagnostics.httpx.post", _post)
    return asked


def test_doctor_shows_the_servers_re_probed_verdicts(monkeypatch, migrated_home) -> None:
    _server_up()
    _local_probe_forbidden(monkeypatch)
    asked = _answer(
        monkeypatch,
        200,
        {
            "native": {"platform": "", "ok": True, "detail": "nested verified"},
            "foreign": [
                {"platform": AMD64, "ok": False, "detail": "rosetta error", "remediation": "fix"}
            ],
        },
    )
    checks = diag.nested_container_checks()
    assert asked and asked[0].endswith("/api/system/nested-support/refresh")
    assert [(c.name, c.ok, c.level) for c in checks] == [
        ("nested containers", True, "blocker"),
        # Amber, not a pass; a warning, so native missions still run and doctor still passes.
        (f"nested containers ({AMD64})", False, "warning"),
    ]
    assert checks[1].remediation == "fix"


def test_doctor_fails_on_the_servers_native_refusal(monkeypatch, migrated_home) -> None:
    _server_up()
    _local_probe_forbidden(monkeypatch)
    _answer(
        monkeypatch,
        200,
        {"native": {"platform": "", "ok": False, "detail": "no nesting", "remediation": "x"}},
    )
    (check,) = diag.nested_container_checks()
    assert check.ok is False and check.level == "blocker"


def test_without_a_server_doctor_probes_locally(monkeypatch, migrated_home) -> None:
    pid_file().unlink(missing_ok=True)
    _local_probe(monkeypatch)
    asked = _answer(monkeypatch, 500, {})
    assert [c.detail for c in diag.nested_container_checks()] == ["local"]
    assert asked == []  # no server from this home: nothing cached to clear, nothing asked


def test_a_stale_pid_file_falls_back_to_the_local_probe(monkeypatch, migrated_home) -> None:
    _server_up()
    _local_probe(monkeypatch)

    def _refused(url, **_kw):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("xorcise.core.cli._diagnostics.httpx.post", _refused)
    assert [c.detail for c in diag.nested_container_checks()] == ["local"]


def test_an_older_server_gets_the_local_probe_and_a_restart_warning(
    monkeypatch, migrated_home
) -> None:
    _server_up()
    _local_probe(monkeypatch)
    _answer(monkeypatch, 405, {"detail": "Method Not Allowed"})
    checks = diag.nested_container_checks()
    assert [c.detail for c in checks][0] == "local"
    assert checks[-1].level == "warning"
    assert checks[-1].ok is False  # drawn amber with its fix, not as a passing tick
    assert "restart" in checks[-1].remediation


def test_a_server_that_cannot_probe_is_undetermined_not_failed(monkeypatch, migrated_home) -> None:
    _server_up()
    _local_probe_forbidden(monkeypatch)
    _answer(monkeypatch, 503, {"detail": "Docker daemon is not reachable"})
    (check,) = diag.nested_container_checks()
    assert check.ok is False and check.level == "warning"  # amber, never a green tick
    assert "undetermined" in check.detail and "not reachable" in check.detail


def test_a_server_still_probing_is_undetermined_not_probed_again(
    monkeypatch, migrated_home
) -> None:
    """A timeout means the server is up and busy, not gone: probing here as well would print a
    verdict the next run does not read, which is the bug #195 is about."""
    _server_up()
    _local_probe_forbidden(monkeypatch)

    def _slow(url, **_kw):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("xorcise.core.cli._diagnostics.httpx.post", _slow)
    (check,) = diag.nested_container_checks()
    assert check.ok is False and check.level == "warning"  # amber, never a green tick
    assert "still probing" in check.detail


@pytest.mark.parametrize(
    ("status", "body"),
    [(502, ["not", "a", "dict"]), (200, ["not", "a", "verdict"]), (200, {"native": {}})],
)
def test_an_unexpected_answer_is_undetermined_not_a_crash(
    monkeypatch, migrated_home, status, body
) -> None:
    _server_up()
    _local_probe_forbidden(monkeypatch)
    _answer(monkeypatch, status, body)
    (check,) = diag.nested_container_checks()
    assert check.ok is False and check.level == "warning"  # amber, never a green tick
    assert "undetermined" in check.detail


def test_doctor_draws_an_undetermined_probe_amber_with_its_fix_not_a_green_tick(
    monkeypatch, migrated_home
) -> None:
    """Guru's #213 review: "undetermined" was printed beside a green tick, which reads as a pass
    when nothing was confirmed. It is drawn as an amber warning with what to do, and it still
    does not fail doctor's verdict (only blockers do)."""
    from xorcise.core.cli.commands.lifecycle import _doctor_line

    _server_up()
    _local_probe_forbidden(monkeypatch)
    _answer(monkeypatch, 503, {"detail": "Docker daemon is not reachable"})
    (check,) = diag.nested_container_checks()
    line = _doctor_line(check)
    assert "✓" not in line and "[warn]![/warn]" in line
    assert "undetermined" in line and "re-run xorcise doctor" in line
    assert check.level == "warning"  # advisory: never flips the verdict
