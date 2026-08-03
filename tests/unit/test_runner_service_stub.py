import base64

import yaml

from xorcise.core.contracts.control import DeployRequest, MissionRef, NetworkSpec, RunState
from xorcise.core.runner.docker import StubDockerDriver
from xorcise.core.runner.service import RunnerControlService


def _req(run_id: str = "run-1") -> DeployRequest:
    return DeployRequest(
        run_id=run_id,
        mission=MissionRef(mission_id="c", image="xorcise/mission-c:0"),
        network=NetworkSpec(tailnet="10.200.1.0/24", auth_key="k"),
    )


def test_deploy_fails_loud_on_empty_auth_key():
    # an unminted router key must fail loud, not deliver an empty TS_AUTHKEY that
    # silently aborts `tailscale up` inside the container.
    import pytest

    svc = RunnerControlService(StubDockerDriver())
    bad = DeployRequest(
        run_id="run-x",
        mission=MissionRef(mission_id="c", image="xorcise/mission-c:0"),
        network=NetworkSpec(tailnet="10.200.1.0/24", auth_key=""),
    )
    with pytest.raises(ValueError, match="auth key"):
        svc.deploy(bad)


def test_deploy_pins_subnets_from_contract_routes_not_recarve():
    # the override subnets come from the routes the fence carved (the contract),
    # NOT a second re-carve in the runner. Pass a route that DIFFERS from what re-carving the
    # tailnet would yield; the pinned subnet must follow the contract route.
    driver = StubDockerDriver()
    RunnerControlService(driver).deploy(
        DeployRequest(
            run_id="run-z",
            mission=MissionRef(mission_id="c", image="xorcise/mission-c:0"),
            network=NetworkSpec(
                tailnet="10.200.9.0/24",  # a re-carve would pin 10.200.9.0/24
                auth_key="k",
                entry_networks=("default",),
                routes=("10.88.0.0/24",),  # but the fence advertised THIS
            ),
        )
    )
    env = dict(driver.specs[0].env)
    assert env["XORCISE_ROUTES"] == "10.88.0.0/24"
    override = yaml.safe_load(base64.b64decode(env["XORCISE_NET_OVERRIDE_B64"]))
    assert override["networks"]["default"]["ipam"]["config"][0]["subnet"] == "10.88.0.0/24"


def test_deploy_delivers_net_override_and_authkey_env():
    driver = StubDockerDriver()
    svc = RunnerControlService(driver, login_server="http://hs:8080")
    svc.deploy(
        DeployRequest(
            run_id="run-9",
            mission=MissionRef(mission_id="c", image="xorcise/mission-c:0"),
            network=NetworkSpec(
                tailnet="10.200.9.0/24",
                auth_key="tskey-abc",
                login_server="http://hs:8080",
                entry_networks=("default",),
                routes=("10.200.9.0/24",),
            ),
        )
    )
    env = dict(driver.specs[0].env)
    assert env["XORCISE_AUTHKEY"] == "tskey-abc"  # the minted key reaches the container
    assert env["XORCISE_LOGIN_SERVER"] == "http://hs:8080"
    assert env["XORCISE_ROUTES"] == "10.200.9.0/24"  # carved entry subnet advertised
    # the override decodes to compose YAML that adds the Tailscale router inner container
    override = yaml.safe_load(base64.b64decode(env["XORCISE_NET_OVERRIDE_B64"]))
    assert "xorcise-router" in override["services"]
    assert override["networks"]["default"]["ipam"]["config"][0]["subnet"] == "10.200.9.0/24"
    # the secret is NOT baked into the override file — only a placeholder
    assert "tskey-abc" not in base64.b64decode(env["XORCISE_NET_OVERRIDE_B64"]).decode()


def test_deploy_delivers_ca_and_extra_hosts_when_airgapped():
    driver = StubDockerDriver()
    svc = RunnerControlService(driver, login_server="https://headscale.local:8443")
    ca_pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    svc.deploy(
        DeployRequest(
            run_id="run-tls",
            mission=MissionRef(mission_id="c", image="xorcise/mission-c:0"),
            network=NetworkSpec(
                tailnet="10.200.1.0/24",
                auth_key="k",
                login_server="https://headscale.local:8443",
                entry_networks=("default",),
                routes=("10.200.1.0/24",),
                ca_cert=ca_pem,
                extra_hosts=("headscale.local:172.17.0.1",),
            ),
        )
    )
    env = dict(driver.specs[0].env)
    # CA delivered as its own base64 env var (entrypoint writes it; never inside the override)
    assert base64.b64decode(env["XORCISE_HEADSCALE_CA_B64"]).decode() == ca_pem
    override = yaml.safe_load(base64.b64decode(env["XORCISE_NET_OVERRIDE_B64"]))
    router = override["services"]["xorcise-router"]
    assert router["extra_hosts"] == ["headscale.local:172.17.0.1"]
    # The router writes this supplied base64 CA inside its own filesystem. A host-daemon compose
    # cannot bind the outer fused container's /mission path on macOS.
    assert "SSL_CERT_FILE=/tmp/headscale-ca.pem" in router["environment"]
    assert "XORCISE_HEADSCALE_CA_B64=${XORCISE_HEADSCALE_CA_B64}" in router["environment"]


def test_deploy_omits_ca_env_when_not_airgapped():
    driver = StubDockerDriver()
    RunnerControlService(driver).deploy(_req())
    assert "XORCISE_HEADSCALE_CA_B64" not in dict(driver.specs[0].env)


def test_deploy_runs_the_fused_image_and_returns_endpoints():
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    ep = svc.deploy(_req())
    assert ep.run_id == "run-1"
    assert "xorcise/mission-c:0" in driver.running[next(iter(driver.running))].image


def test_deploy_is_idempotent():
    svc = RunnerControlService(StubDockerDriver())
    assert svc.deploy(_req()) == svc.deploy(_req())


def test_teardown_stops_container_and_status_torn_down():
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    svc.deploy(_req())
    svc.teardown("run-1")
    assert driver.stopped  # the container was stopped
    assert svc.status("run-1").state == RunState.TORN_DOWN


def test_teardown_is_idempotent():
    svc = RunnerControlService(StubDockerDriver())
    svc.deploy(_req())
    assert svc.teardown("run-1").ok
    assert svc.teardown("run-1").ok


def test_status_reports_live_container_across_a_restart():
    # a FRESH service (empty _deployed — after a server restart / a second worker)
    # still reports READY for a container that is live in Docker under the run-id name, by
    # inspecting it by name instead of trusting the in-memory cache. This is the true deploy state
    # the reconcile-on-startup loop adopts.
    driver = StubDockerDriver()
    RunnerControlService(driver).deploy(_req("run-live"))  # process A deploys

    fresh = RunnerControlService(driver)  # process B: empty _deployed, same Docker
    assert fresh.status("run-live").state == RunState.READY
    assert fresh.collect_targets("run-live").run_id == "run-live"


def test_status_and_collect_targets_not_found_when_container_absent():
    # no cache entry AND no live container ⇒ NotFound (the reconcile loop reads
    # this as "gone" → the run is unrecoverable), never a false READY.
    import pytest

    from xorcise.core.contracts.errors import NotFoundError

    fresh = RunnerControlService(StubDockerDriver())
    with pytest.raises(NotFoundError):
        fresh.status("ghost")
    with pytest.raises(NotFoundError):
        fresh.collect_targets("ghost")


def test_teardown_stops_container_by_name_across_a_restart():
    # a teardown from a FRESH service (empty _deployed — e.g. after a server
    # restart or from a second worker) still stops the container. The container is named by run_id,
    # so teardown stops it by name instead of relying on the in-memory handle.
    driver = StubDockerDriver()
    RunnerControlService(driver).deploy(_req("run-restart"))  # "process A" deploys
    assert not driver.stopped

    fresh = RunnerControlService(driver)  # "process B": empty _deployed, same Docker
    result = fresh.teardown("run-restart")
    assert "run-restart" in driver.stopped_by_name  # stopped by run-id name, not a cached handle
    assert result.ok
