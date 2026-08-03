from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from xorcise.core.rest.routers.runcontrol import (
    get_connect,
    get_join_script,
    get_tailscale_tarball,
)


def _settings(
    *,
    ca_cert_path: str = "",
    host_alias: str = "headscale.local:172.17.0.1",
    tailscale_cache_root: str = "/tmp/xorcise-ts-cache",
) -> SimpleNamespace:
    return SimpleNamespace(
        headscale_ca_cert=ca_cert_path,
        headscale_url="https://headscale.local:8443",
        headscale_advertise_host="",
        headscale_port=8443,
        host="localhost",
        headscale_host_alias=host_alias,
        tailscale_cache_root=tailscale_cache_root,
    )


def _req(rid: str) -> Request:
    """A minimal Starlette Request whose URL ends in /join.sh, so _runcontrol_base() resolves."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("127.0.0.1", 3001),
            "path": f"/api/runs/{rid}/join.sh",
            "headers": [],
            "query_string": b"",
        }
    )


def _make_run(
    run_control_key: str = "rk", join_key: str = "tskey-secret", budget_seconds: int = 0
) -> str:
    from xorcise.core import runs

    return runs.create_run(
        agent_id="a1",
        mission="c",
        run_control_key=run_control_key,
        join_key=join_key,
        budget_seconds=budget_seconds,
    ).run_id


@pytest.mark.unit
def test_connect_returns_bundle_for_authenticated_caller(migrated_home, monkeypatch):
    import xorcise.core.rest.routers.runcontrol as rc

    monkeypatch.setattr(rc, "get_settings", lambda: _settings())
    rid = _make_run()
    resp = get_connect(run_id=rid, authorization="Bearer rk")
    assert resp.join_key == "tskey-secret"
    # non-air-gapped default: no CA, login_server returned as-is (base)
    assert resp.ca_cert == ""
    assert resp.login_server == "https://headscale.local:8443"


@pytest.mark.unit
def test_connect_kicks_the_join_reconciler(migrated_home, monkeypatch):
    # Fetching the join bundle means the agent is joining the tailnet NOW — /connect kicks the
    # join-confirm reconciler so the terrain agent<->Headscale edge activates without depending on
    # anyone viewing /terrain2 at that instant. get_connect imports the kicker function-locally,
    # so patch it on its source module.
    import xorcise.core.rest.join_reconcile as jr
    import xorcise.core.rest.routers.runcontrol as rc

    monkeypatch.setattr(rc, "get_settings", lambda: _settings())
    kicked: list[str] = []
    monkeypatch.setattr(jr, "maybe_reconcile_join", lambda rid: kicked.append(rid))
    rid = _make_run()
    get_connect(run_id=rid, authorization="Bearer rk")
    assert kicked == [rid]


@pytest.mark.unit
def test_connect_air_gapped_returns_ca_and_by_ip_login_server(migrated_home, monkeypatch, tmp_path):
    import xorcise.core.rest.routers.runcontrol as rc

    pem = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text(pem)
    monkeypatch.setattr(rc, "get_settings", lambda: _settings(ca_cert_path=str(ca_file)))
    rid = _make_run()
    resp = get_connect(run_id=rid, authorization="Bearer rk")
    assert resp.ca_cert == pem
    # air-gapped: login_server rewritten to the control-plane IP
    assert resp.login_server == "https://172.17.0.1:8443"


@pytest.mark.unit
def test_connect_401_without_or_with_wrong_bearer(migrated_home, monkeypatch):
    import xorcise.core.rest.routers.runcontrol as rc

    monkeypatch.setattr(rc, "get_settings", lambda: _settings())
    rid = _make_run()
    with pytest.raises(HTTPException) as ei:
        get_connect(run_id=rid, authorization=None)
    assert ei.value.status_code == 401
    with pytest.raises(HTTPException) as ei2:
        get_connect(run_id=rid, authorization="Bearer wrong")
    assert ei2.value.status_code == 401


@pytest.mark.unit
def test_join_script_serves_runnable_bundle_for_authenticated_caller(migrated_home, monkeypatch):
    import xorcise.core.rest.routers.runcontrol as rc

    monkeypatch.setattr(rc, "get_settings", lambda: _settings())
    rid = _make_run(join_key="tskey-secret")
    resp = get_join_script(request=_req(rid), run_id=rid, authorization="Bearer rk")
    body = bytes(resp.body).decode()
    assert resp.media_type == "text/x-shellscript"
    assert body.startswith("#!")  # runnable via `curl ... | sh`
    assert "tskey-secret" in body  # the per-run authkey is baked in
    assert "https://headscale.local:8443" in body  # login server from settings
    assert "tailscale" in body
    # the run-control base (from the request) + bearer are baked so the script can pull the client
    assert 'RC_BASE="http://127.0.0.1:3001/api/runs/' in body
    assert "/tailscale.tgz?arch=$A" in body


@pytest.mark.unit
def test_join_script_kicks_the_join_reconciler(migrated_home, monkeypatch):
    # The headless harness joins via `curl .../join.sh | sh`, so fetching the join script (not
    # /connect) is the real "agent is joining now" signal — it must kick the reconciler too.
    import xorcise.core.rest.join_reconcile as jr
    import xorcise.core.rest.routers.runcontrol as rc

    monkeypatch.setattr(rc, "get_settings", lambda: _settings())
    kicked: list[str] = []
    monkeypatch.setattr(jr, "maybe_reconcile_join", lambda rid: kicked.append(rid))
    rid = _make_run()
    get_join_script(request=_req(rid), run_id=rid, authorization="Bearer rk")
    assert kicked == [rid]


@pytest.mark.unit
def test_join_script_air_gapped_bakes_ca_and_by_ip_login(migrated_home, monkeypatch, tmp_path):
    import xorcise.core.rest.routers.runcontrol as rc

    pem = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text(pem)
    monkeypatch.setattr(rc, "get_settings", lambda: _settings(ca_cert_path=str(ca_file)))
    rid = _make_run()
    body = bytes(get_join_script(request=_req(rid), run_id=rid, authorization="Bearer rk").body)
    text = body.decode()
    assert "BEGIN CERTIFICATE" in text  # CA embedded so the daemon trusts the control plane
    assert "https://172.17.0.1:8443" in text  # login server rewritten to the control-plane IP


@pytest.mark.unit
def test_join_script_binds_reaper_cap_to_the_run_budget(migrated_home, monkeypatch):
    # The reaper's hard-cap backstop is bound to this run's budget (+ a margin so the authoritative
    # 409 fires first), not a blanket 24h — so a daemon leaked by a hard-killed box self-reaps in
    # ~run-lifetime. A run with no budget falls back to the 24h blanket cap.
    import xorcise.core.rest.routers.runcontrol as rc

    monkeypatch.setattr(rc, "get_settings", lambda: _settings())
    rid = _make_run(budget_seconds=600)
    body = bytes(get_join_script(request=_req(rid), run_id=rid, authorization="Bearer rk").body)
    text = body.decode()
    assert "${XORCISE_REAP_MAX:-1200}" in text  # 600s budget + 600s margin
    assert "86400" not in text  # the blanket 24h default is not used when the run is budgeted


@pytest.mark.unit
def test_join_script_reaper_cap_falls_back_to_24h_when_unbudgeted(migrated_home, monkeypatch):
    import xorcise.core.rest.routers.runcontrol as rc

    monkeypatch.setattr(rc, "get_settings", lambda: _settings())
    rid = _make_run(budget_seconds=0)  # unbounded / unset budget
    body = bytes(get_join_script(request=_req(rid), run_id=rid, authorization="Bearer rk").body)
    assert "${XORCISE_REAP_MAX:-86400}" in body.decode()


@pytest.mark.unit
def test_join_script_401_without_bearer(migrated_home, monkeypatch):
    import xorcise.core.rest.routers.runcontrol as rc

    monkeypatch.setattr(rc, "get_settings", lambda: _settings())
    rid = _make_run()
    with pytest.raises(HTTPException) as ei:
        get_join_script(request=_req(rid), run_id=rid, authorization=None)
    assert ei.value.status_code == 401


@pytest.mark.unit
def test_tailscale_tarball_serves_cached_client(migrated_home, monkeypatch, tmp_path):
    import xorcise.core.rest.routers.runcontrol as rc
    from xorcise.core.runs.join import TAILSCALE_CLIENT_VERSION

    monkeypatch.setattr(rc, "get_settings", lambda: _settings(tailscale_cache_root=str(tmp_path)))
    # Pre-seed the cache so the endpoint serves it without touching the network.
    cached = tmp_path / f"tailscale_{TAILSCALE_CLIENT_VERSION}_amd64.tgz"
    cached.write_bytes(b"TGZ")
    rid = _make_run()
    resp = get_tailscale_tarball(run_id=rid, arch="amd64", authorization="Bearer rk")
    assert str(resp.path) == str(cached)
    assert resp.media_type == "application/gzip"


@pytest.mark.unit
def test_tailscale_tarball_400_on_unsupported_arch(migrated_home, monkeypatch):
    import xorcise.core.rest.routers.runcontrol as rc

    monkeypatch.setattr(rc, "get_settings", lambda: _settings())
    rid = _make_run()
    with pytest.raises(HTTPException) as ei:
        get_tailscale_tarball(run_id=rid, arch="mips; rm -rf /", authorization="Bearer rk")
    assert ei.value.status_code == 400


@pytest.mark.unit
def test_tailscale_tarball_401_without_bearer(migrated_home, monkeypatch):
    import xorcise.core.rest.routers.runcontrol as rc

    monkeypatch.setattr(rc, "get_settings", lambda: _settings())
    rid = _make_run()
    with pytest.raises(HTTPException) as ei:
        get_tailscale_tarball(run_id=rid, arch="amd64", authorization=None)
    assert ei.value.status_code == 401


@pytest.mark.unit
def test_tailscale_tarball_502_when_server_cannot_fetch(migrated_home, monkeypatch, tmp_path):
    import xorcise.core.rest.routers.runcontrol as rc
    import xorcise.core.runs.tailscale_dist as dist

    monkeypatch.setattr(rc, "get_settings", lambda: _settings(tailscale_cache_root=str(tmp_path)))

    def boom(*a, **k):
        raise dist.TailscaleDistError("no egress")

    monkeypatch.setattr(dist, "ensure_tarball", boom)
    rid = _make_run()
    with pytest.raises(HTTPException) as ei:
        get_tailscale_tarball(run_id=rid, arch="amd64", authorization="Bearer rk")
    assert ei.value.status_code == 502  # join.sh treats this as its cue to fall back to the CDN
