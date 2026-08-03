"""node_online(user) — Phase 3 terrain-map prep: confirm the agent actually joined the tailnet
by checking Headscale's own `nodes list` for a node owned by that user reporting online:true.

Mirrors the existing parse_nodes_for_user helper (used by the delete paths) but also reads the
`online` field. Never raises — a missing user or malformed JSON is just "not online" (False)."""

from xorcise.core.headscale.cli import (
    DockerExecHeadscaleCli,
    StubHeadscaleCli,
    parse_node_online_for_user,
)


def test_node_online_false_when_headscale_prints_null(monkeypatch):
    # `headscale nodes list -o json` prints the literal `null` (not `[]`) when there are no
    # nodes; `_exec_json` must coerce that to [] so node_online returns False rather than raising
    # on `for n in None`.
    cli = DockerExecHeadscaleCli("hs")
    monkeypatch.setattr(cli, "_exec", lambda *a: "null")
    assert cli.node_online("run-abc-agent") is False


def test_parse_node_online_true_when_users_node_reports_online():
    nodes = [
        {"id": "3", "user": {"name": "agent-1"}, "name": "abc-agent-1", "online": True},
        {"id": "4", "user": {"name": "agent-2"}, "name": "abc-agent-2", "online": False},
    ]
    assert parse_node_online_for_user(nodes, "agent-1") is True


def test_parse_node_online_false_when_users_node_reports_offline():
    nodes = [
        {"id": "4", "user": {"name": "agent-2"}, "name": "abc-agent-2", "online": False},
    ]
    assert parse_node_online_for_user(nodes, "agent-2") is False


def test_parse_node_online_true_if_any_of_users_nodes_is_online():
    # a user could have more than one registered node (e.g. re-join after a crash); one online
    # node is enough.
    nodes = [
        {"id": "5", "user": {"name": "agent-1"}, "name": "old", "online": False},
        {"id": "6", "user": {"name": "agent-1"}, "name": "new", "online": True},
    ]
    assert parse_node_online_for_user(nodes, "agent-1") is True


def test_parse_node_online_false_when_user_absent():
    nodes = [
        {"id": "4", "user": {"name": "agent-2"}, "name": "abc-agent-2", "online": True},
    ]
    assert parse_node_online_for_user(nodes, "agent-1") is False


def test_parse_node_online_false_when_nodes_list_empty():
    assert parse_node_online_for_user([], "agent-1") is False


def test_parse_node_online_false_on_malformed_node_entries():
    # missing "online" key, non-dict "user", and a node id under the wrong key must never raise —
    # they just fail to count as online.
    nodes = [
        {"id": "7", "user": {"name": "agent-1"}},  # no "online" key at all
        {"id": "8", "user": "agent-1"},  # user is a bare string, not a dict
        "not-even-a-dict",  # a malformed entry in the list
    ]
    assert parse_node_online_for_user(nodes, "agent-1") is False


def test_stub_node_online_defaults_false_and_is_settable():
    # the ABC is minimal; the stub gives tests a settable value rather than shelling to Docker.
    cli = StubHeadscaleCli()
    assert cli.node_online("agent-1") is False
    cli.online_users.add("agent-1")
    assert cli.node_online("agent-1") is True
    assert cli.node_online("agent-2") is False
