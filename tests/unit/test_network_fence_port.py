from xorcise.core.headscale import NetworkController, StubHeadscaleCli
from xorcise.core.orchestration.clients.headscale_client import HeadscaleFenceClient
from xorcise.core.orchestration.ports import NetworkFencePort


def _client() -> tuple[HeadscaleFenceClient, StubHeadscaleCli]:
    cli = StubHeadscaleCli()
    ctrl = NetworkController(cli, router_tag="tag:router", orchestrator_user="orchestrator")
    return HeadscaleFenceClient(ctrl), cli


def test_is_a_network_fence_port():
    client, _ = _client()
    assert isinstance(client, NetworkFencePort)


def test_create_mints_and_teardown_revokes():
    # teardown reclaims the agent user by its run-derived name (run-<run_id[:8]>-agent), the
    # convention create + the ACL provider use, so create with that name.
    client, cli = _client()
    net = client.create_run_network("abcd1234ef00", "run-abcd1234-agent", ["10.200.1.0/24"])
    assert net.auth_key in cli.keys_minted
    client.teardown_run_network("abcd1234ef00")
    assert "run-abcd1234-agent" in cli.users_deleted
