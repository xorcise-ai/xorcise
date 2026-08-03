from xorcise.core.headscale.cli import (
    HeadscaleCli,
    StubHeadscaleCli,
    parse_node_id_by_name,
    parse_nodes_for_user,
    parse_user_id,
)


def test_stub_is_a_headscale_cli():
    assert isinstance(StubHeadscaleCli(), HeadscaleCli)


def test_stub_records_user_key_policy_and_delete():
    cli = StubHeadscaleCli()
    cli.create_user("agent-1")
    key = cli.create_preauth_key("agent-1", expiration="2h")
    cli.apply_acl_policy('{"acls": []}')
    n = cli.delete_nodes_for_user("agent-1")
    cli.delete_user("agent-1")

    assert "agent-1" in cli.users_created
    assert key in cli.keys_minted and key
    assert cli.policies_applied[-1] == '{"acls": []}'
    assert n == 0
    assert "agent-1" in cli.users_deleted


def test_parse_user_id():
    users = [{"id": "7", "name": "agent-1"}, {"id": "9", "name": "orchestrator"}]
    assert parse_user_id(users, "agent-1") == 7
    assert parse_user_id(users, "nobody") is None


def test_parse_nodes_for_user():
    nodes = [
        {"id": "3", "user": {"name": "agent-1"}},
        {"id": "4", "user": {"name": "agent-2"}},
        {"id": "5", "user": {"name": "agent-1"}},
    ]
    assert parse_nodes_for_user(nodes, "agent-1") == [3, 5]


def test_parse_node_id_by_name():
    # resolves the per-run router node id (owned by the shared orchestrator user, so not
    # addressable by user) via its name or given_name; None when absent.
    nodes = [
        {"id": "1", "name": "abc123-router", "given_name": "abc123-router"},
        {"id": "2", "name": "somehost", "given_name": "abc123-router-2"},
    ]
    assert parse_node_id_by_name(nodes, "abc123-router") == 1
    assert parse_node_id_by_name(nodes, "abc123-router-2") == 2  # matches given_name
    assert parse_node_id_by_name(nodes, "ghost-router") is None
