import pytest

import xorcise.core.headscale.provision as provision
from xorcise.core.cli.commands.lifecycle import _headscale_plan
from xorcise.core.config import Settings


def _s(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _no_managed_block(monkeypatch):
    """`_maybe_provision_headscale` reads the real ~/.xorcise/config.toml to learn which URL
    OUR managed block wrote. Without this, every test here would flip behaviour depending on
    whether the developer happens to have run `xorcise up`. Default to "no managed block"
    (the fresh-install state); tests that care override it."""
    monkeypatch.setattr(provision, "managed_url", lambda cp: "")


@pytest.mark.parametrize(
    "stub,docker_ok,url,expected",
    [
        (True, True, "", "skip-stub"),
        (True, False, "", "skip-stub"),  # stub wins even without docker
        (False, False, "", "fail-docker"),
        (False, True, "https://x:8443", "external"),
        (False, True, "", "local"),
    ],
)
def test_headscale_plan(stub, docker_ok, url, expected):
    assert _headscale_plan(_s(headscale_url=url), stub=stub, docker_ok=docker_ok) == expected


def test_maybe_provision_local_when_default(monkeypatch):
    import xorcise.core.cli.commands.lifecycle as lc

    called = {}

    def _fake_up(wd, cp, **k):
        called["up"] = (wd, cp)
        return provision.ProvisionResult("https://h:8443", "/ca", "h:1.2.3.4", "1.2.3.4")

    monkeypatch.setattr(provision, "ensure_up", _fake_up)
    plan = lc._maybe_provision_headscale(_s(headscale_url=""), stub=False, docker_ok=True)
    assert plan == "local"
    assert "up" in called


def test_maybe_provision_threads_deployment_topology_to_ensure_up(monkeypatch):
    # the topology must reach ensure_up so the orchestrator collector /32 route is
    # advertised only in distributed. Guards against a silent drop of the kwarg (would default
    # to "local" unnoticed).
    import xorcise.core.cli.commands.lifecycle as lc

    captured = {}

    def _fake_up(wd, cp, **k):
        captured.update(k)
        return provision.ProvisionResult("https://h:8443", "/ca", "h:1.2.3.4", "1.2.3.4")

    monkeypatch.setattr(provision, "ensure_up", _fake_up)
    lc._maybe_provision_headscale(
        _s(headscale_url=""), stub=False, docker_ok=True, deployment_topology="distributed"
    )
    assert captured.get("deployment_topology") == "distributed"


def test_maybe_provision_skips_when_external_or_stub(monkeypatch):
    import xorcise.core.cli.commands.lifecycle as lc

    def _boom(*a, **k):
        pytest.fail("must not provision")

    monkeypatch.setattr(provision, "ensure_up", _boom)
    monkeypatch.setattr(lc, "external_control_plane", lambda url: _ok_check(url))
    ext = lc._maybe_provision_headscale(
        _s(headscale_url="https://x:8443"), stub=False, docker_ok=True
    )
    assert ext == "external"
    skipped = lc._maybe_provision_headscale(_s(headscale_url=""), stub=True, docker_ok=True)
    assert skipped == "skip-stub"


def _ok_check(url: str):
    from xorcise.core.cli._diagnostics import Check

    return Check("control plane", True, f"{url} answered")


def test_headscale_plan_treats_our_own_managed_url_as_local():
    """A successful local `up` writes headscale_url into the managed config block. On the
    NEXT `up` that value must not read as a user-configured REMOTE plane — otherwise
    provisioning is skipped, no container exists, and every run creation 503s."""
    ours = _s(headscale_url="https://172.17.0.1:443")
    assert _headscale_plan(ours, stub=False, docker_ok=True, ours=True) == "local"
    assert _headscale_plan(ours, stub=False, docker_ok=True, ours=False) == "external"


def test_maybe_provision_reprovisions_when_we_own_the_workdir(monkeypatch):
    """The regression that wedged a live instance: containers gone (reboot / docker prune)
    but the managed URL still present, so `up` reported success and provisioned nothing."""
    import xorcise.core.cli.commands.lifecycle as lc

    called = {}

    def _fake_up(wd, cp, **k):
        called["up"] = True
        return provision.ProvisionResult("https://h:8443", "/ca", "h:1.2.3.4", "1.2.3.4")

    monkeypatch.setattr(provision, "ensure_up", _fake_up)
    monkeypatch.setattr(provision, "managed_url", lambda cp: "https://172.17.0.1:443")
    plan = lc._maybe_provision_headscale(
        _s(headscale_url="https://172.17.0.1:443"), stub=False, docker_ok=True
    )
    assert plan == "local"
    assert called.get("up") is True


def test_maybe_provision_external_fails_when_the_plane_is_unreachable(monkeypatch):
    """`up` must not report a SAVED PREFERENCE as a verified connection: it used to print
    'using external control plane at <url>' from config alone, so a stale URL looked like
    a healthy start and every subsequent run creation failed with a 503."""
    import typer

    import xorcise.core.cli.commands.lifecycle as lc
    from xorcise.core.cli._diagnostics import Check

    monkeypatch.setattr(
        lc,
        "external_control_plane",
        lambda url: Check("control plane", False, f"{url} is unreachable", "clear it"),
    )
    with pytest.raises(typer.Exit) as exc:
        lc._maybe_provision_headscale(
            _s(headscale_url="https://dead:443"), stub=False, docker_ok=True
        )
    assert exc.value.exit_code == 1


def test_maybe_teardown_only_when_owned(monkeypatch):
    import xorcise.core.cli.commands.lifecycle as lc
    from xorcise.core.cli._diagnostics import Check

    torn = []

    def _teardown(wd, cp):
        torn.append(True)
        return True

    monkeypatch.setattr(provision, "is_owned", lambda wd: True)
    monkeypatch.setattr(provision, "teardown", _teardown)
    monkeypatch.setattr(lc, "docker_daemon", lambda: Check("docker", True, "ok"))
    lc._maybe_teardown_headscale()
    assert torn == [True]


def test_an_operator_configured_remote_is_not_mistaken_for_ours(monkeypatch):
    """The regression the first fix introduced: keying off an ownership MARKER meant that
    setting a remote plane while a local one was still provisioned left `up` silently using
    local — discarding an explicit `config set-network --headscale-url`. The managed block
    holds OUR url, so a different url is the operator's intent and must resolve to external."""
    import xorcise.core.cli.commands.lifecycle as lc
    from xorcise.core.cli._diagnostics import Check

    def _boom(*a, **k):
        pytest.fail("must not provision locally when the operator asked for a remote")

    monkeypatch.setattr(provision, "ensure_up", _boom)
    # We provisioned https://local:443 earlier; the operator has since asked for https://remote:443.
    monkeypatch.setattr(provision, "managed_url", lambda cp: "https://local:443")
    reachable = Check("control plane", True, "ok")
    monkeypatch.setattr(lc, "external_control_plane", lambda url: reachable)
    monkeypatch.setattr(lc, "control_plane", lambda *a, **k: reachable)
    plan = lc._maybe_provision_headscale(
        _s(headscale_url="https://remote:443"), stub=False, docker_ok=True
    )
    assert plan == "external"


def test_external_warns_when_the_local_control_plane_container_is_missing(monkeypatch, capsys):
    """Reaching the URL is not the whole dependency: run creation shells into a LOCAL
    container, so on a runner host a reachable remote URL alone can still leave every run
    503-ing. `up` must say so rather than imply readiness."""
    import xorcise.core.cli.commands.lifecycle as lc
    from xorcise.core.cli._diagnostics import Check

    monkeypatch.setattr(provision, "managed_url", lambda cp: "")
    monkeypatch.setattr(
        lc, "external_control_plane", lambda url: Check("control plane", True, "ok")
    )
    missing = Check("control plane", False, "'headscale' is not reachable", "fix")
    monkeypatch.setattr(lc, "control_plane", lambda *a, **k: missing)
    # use_stubs must be explicit: the suite forces stub mode globally, and stub mode has no
    # control-plane container by design, so the warning would (correctly) never fire.
    plan = lc._maybe_provision_headscale(
        _s(headscale_url="https://remote:443", role="all", use_stubs=False),
        stub=False,
        docker_ok=True,
    )
    assert plan == "external"  # still starts — it is a warning, not a refusal
    err = capsys.readouterr().err
    assert "warning" in err.lower()
    assert "run creation will fail" in err


def test_maybe_teardown_skips_gracefully_when_docker_daemon_down(monkeypatch, capsys):
    # Regression: a stopped Docker daemon must NOT crash `xorcise down`. When the daemon is
    # unreachable there is nothing to tear down, so teardown is skipped (ownership marker kept for a
    # later real teardown) instead of raising ProvisionError from `docker compose down -v`.
    import xorcise.core.cli.commands.lifecycle as lc
    from xorcise.core.cli._diagnostics import Check

    monkeypatch.setattr(provision, "is_owned", lambda wd: True)
    monkeypatch.setattr(lc, "docker_daemon", lambda: Check("docker", False, "unreachable"))

    def _must_not_teardown(wd, cp):
        pytest.fail("teardown must not run when the docker daemon is unreachable")

    monkeypatch.setattr(provision, "teardown", _must_not_teardown)
    lc._maybe_teardown_headscale()  # must not raise
    assert "Docker daemon" in capsys.readouterr().out
