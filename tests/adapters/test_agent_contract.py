from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.agent import (
    GENERIC_KIND,
    AgentDeclaration,
    AgentEntry,
    closest_kind,
    kind_warning,
    normalise_kind,
)


def test_declaration_requires_name_only():
    d = AgentDeclaration(name="alpha")
    assert d.name == "alpha"
    assert d.endpoint is None and d.otel is None
    assert d.launch_mode is None


def test_entry_round_trips_through_json():
    entry = AgentEntry(
        id="abc",
        name="alpha",
        endpoint="http://agent.local",
        otel="service.name=alpha",
        kind="generic",
        created_at=datetime(2026, 6, 17, tzinfo=UTC),
    )
    restored = AgentEntry.model_validate_json(entry.model_dump_json())
    assert restored == entry


def test_a_declaration_may_leave_the_kind_out_but_a_stored_entry_always_has_one():
    # #141: callers that never chose a harness keep working; what the server returns names one.
    assert AgentDeclaration(name="alpha").kind is None
    created = datetime(2026, 6, 17, tzinfo=UTC)
    with pytest.raises(ValidationError, match="kind"):
        AgentEntry(id="abc", name="alpha", created_at=created)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="kind"):
        AgentEntry(id="abc", name="alpha", kind=None, created_at=created)  # type: ignore[arg-type]
    schema = AgentEntry.model_json_schema()
    assert "kind" in schema["required"]
    assert schema["properties"]["kind"] == {"title": "Kind", "type": "string"}


def test_a_blank_kind_is_stored_as_generic_and_a_real_one_loses_only_its_padding():
    assert normalise_kind(None) == GENERIC_KIND == "generic"
    assert normalise_kind("") == normalise_kind(" \t\n") == "generic"
    assert normalise_kind(" openhands ") == "openhands"
    assert normalise_kind("My-Agent") == "My-Agent"  # case is the operator's to choose


_KNOWN = ("claude-code", "codex", "openhands")


def test_kind_warning_is_silent_for_a_registered_or_blank_kind():
    for kind in (None, "", "   ", "generic", "openhands", " codex "):
        assert kind_warning(kind, _KNOWN) is None, kind


def test_kind_warning_names_the_known_kinds_and_the_closest_one():
    warning = kind_warning("openhand", _KNOWN)
    assert warning == (
        "'openhand' is not a registered harness (known: claude-code, codex, generic, openhands); "
        "did you mean 'openhands'? Runs may replay with the generic adapter."
    )
    assert kind_warning("Claude-Code", _KNOWN) is not None  # kinds are case-sensitive...
    assert closest_kind("Claude-Code", _KNOWN) == "claude-code"  # ...the suggestion is not
    assert kind_warning(" my-agent-cli ", _KNOWN) == (
        "'my-agent-cli' is not a registered harness (known: claude-code, codex, generic, "
        "openhands). Runs may replay with the generic adapter."
    )
    assert closest_kind("my-agent-cli", _KNOWN) is None


def test_launch_template_rejects_unknown_placeholders():
    with pytest.raises(ValidationError, match="unsupported launch command placeholder"):
        AgentDeclaration(name="alpha", launch_command_template="agent {unknown}")


def test_launch_mode_accepts_only_known_execution_contexts():
    assert AgentDeclaration(name="alpha", launch_mode="container").launch_mode == "container"
    with pytest.raises(ValidationError):
        AgentDeclaration(name="alpha", launch_mode="remote")  # type: ignore[arg-type]
