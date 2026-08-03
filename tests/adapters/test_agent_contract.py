from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.agent import AgentDeclaration, AgentEntry


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
        created_at=datetime(2026, 6, 17, tzinfo=UTC),
    )
    restored = AgentEntry.model_validate_json(entry.model_dump_json())
    assert restored == entry


def test_launch_template_rejects_unknown_placeholders():
    with pytest.raises(ValidationError, match="unsupported launch command placeholder"):
        AgentDeclaration(name="alpha", launch_command_template="agent {unknown}")


def test_launch_mode_accepts_only_known_execution_contexts():
    assert AgentDeclaration(name="alpha", launch_mode="container").launch_mode == "container"
    with pytest.raises(ValidationError):
        AgentDeclaration(name="alpha", launch_mode="remote")  # type: ignore[arg-type]
