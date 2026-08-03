from __future__ import annotations

import pytest

from xorcise.core import agents


def test_register_then_list(migrated_home):
    agents.register(name="alpha", endpoint="http://a", otel="service.name=alpha")
    listed = agents.list_agents()
    assert [a.name for a in listed] == ["alpha"]
    assert listed[0].id and listed[0].created_at is not None


def test_duplicate_name_rejected(migrated_home):
    agents.register(name="alpha")
    with pytest.raises(agents.DuplicateAgentError):
        agents.register(name="alpha")


def test_remove_existing_returns_true_and_delists(migrated_home):
    agents.register(name="alpha")
    assert agents.remove("alpha") is True
    assert agents.list_agents() == []


def test_remove_missing_returns_false(migrated_home):
    assert agents.remove("ghost") is False


def test_get_returns_entry_or_none(migrated_home):
    assert agents.get("ghost") is None
    agents.register(name="alpha", endpoint="http://a")
    found = agents.get("alpha")
    assert found is not None and found.name == "alpha" and found.id


def test_register_round_trips_disclosed_model(migrated_home):
    entry = agents.register(name="a1", endpoint="http://x", otel=None, model="claude-opus-4-8")
    assert entry.model == "claude-opus-4-8"
    fetched = agents.get("a1")
    assert fetched is not None and fetched.model == "claude-opus-4-8"


def test_register_without_model_is_none(migrated_home):
    assert agents.register(name="a2").model is None


def test_register_starts_at_version_1(migrated_home):
    assert agents.register(name="a1").version == 1


def test_update_agent_bumps_version_same_id(migrated_home):
    e1 = agents.register(name="a1", endpoint="http://x", model="m1")
    e2 = agents.update_agent("a1", endpoint="http://y", model="m2")
    assert e2 is not None
    assert e2.id == e1.id  # SAME identity
    assert e2.version == 2  # bumped
    assert e2.endpoint == "http://y" and e2.model == "m2"  # declaration updated
    fetched = agents.get("a1")
    assert fetched is not None and fetched.version == 2


def test_update_absent_agent_returns_none(migrated_home):
    assert agents.update_agent("ghost") is None


def test_register_duplicate_still_raises(migrated_home):
    agents.register(name="a1")
    with pytest.raises(agents.DuplicateAgentError):
        agents.register(name="a1")  # POST behavior UNCHANGED


def test_register_round_trips_kind(migrated_home):
    entry = agents.register(name="scout", kind="openhands")
    assert entry.kind == "openhands"
    fetched = agents.get("scout")
    assert fetched is not None and fetched.kind == "openhands"


def test_register_without_kind_is_none(migrated_home):
    assert agents.register(name="a-nokind").kind is None


def test_register_round_trips_launch_overrides_and_empty_lists(migrated_home):
    entry = agents.register(
        name="custom-launch",
        kind="codex",
        launch_command_template="my-codex {mission}",
        launch_tips=("tip one",),
        mission_preamble=(),
        launch_mode="container",
    )
    assert entry.launch_command_template == "my-codex {mission}"
    assert entry.launch_tips == ("tip one",)
    assert entry.mission_preamble == ()
    assert entry.launch_mode == "container"
    assert agents.get_by_id(entry.id) == entry


def test_update_agent_replaces_kind_and_bumps_version(migrated_home):
    agents.register(name="scout", kind="openhands")
    e2 = agents.update_agent("scout", kind="claude-code")
    assert e2 is not None and e2.kind == "claude-code" and e2.version == 2


def test_update_agent_can_reset_launch_overrides_to_provider_defaults(migrated_home):
    agents.register(
        name="scout",
        launch_command_template="custom {mission}",
        launch_tips=(),
        mission_preamble=("custom preamble",),
        launch_mode="container",
    )
    entry = agents.update_agent("scout")
    assert entry is not None
    assert entry.launch_command_template is None
    assert entry.launch_tips is None
    assert entry.mission_preamble is None
    assert entry.launch_mode is None


def test_update_agent_renames_same_id_bumps_version(migrated_home):
    e1 = agents.register(name="scout", endpoint="http://x")
    e2 = agents.update_agent("scout", new_name="pathfinder", endpoint="http://x")
    assert e2 is not None
    assert e2.name == "pathfinder"
    assert e2.id == e1.id  # SAME identity — rename, not re-register
    assert e2.version == 2  # a rename is a re-declaration at a new version
    assert agents.get("scout") is None
    fetched = agents.get("pathfinder")
    assert fetched is not None and fetched.id == e1.id


def test_update_agent_rename_to_taken_name_raises(migrated_home):
    agents.register(name="scout")
    agents.register(name="pathfinder")
    with pytest.raises(agents.DuplicateAgentError):
        agents.update_agent("scout", new_name="pathfinder")
    # The failed rename left both agents untouched.
    assert agents.get("scout") is not None


def test_update_agent_rename_to_same_name_is_plain_update(migrated_home):
    agents.register(name="scout")
    e2 = agents.update_agent("scout", new_name="scout", model="m2")
    assert e2 is not None and e2.name == "scout" and e2.version == 2


def test_update_agent_rename_absent_returns_none(migrated_home):
    assert agents.update_agent("ghost", new_name="anything") is None
