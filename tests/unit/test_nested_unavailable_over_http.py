"""The nested-container refusal has to survive the HTTP hop.

`xorcise run create` POSTs to the server, so the check runs SERVER-side and its exception never
reaches the CLI's own error guard. NestedContainersUnavailableError is a ContractError, which is
NOT a RuntimeError — so without an explicit handler it falls through the runs router as an
unhandled 500, FastAPI replaces the body with a generic message, and every word of the diagnosis
and remediation is lost exactly when the operator needs it. That is the regression these pin.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xorcise.core.contracts.errors import NestedContainersUnavailableError
from xorcise.core.roles.boot.role_all import build_rest_app

MESSAGE = (
    "this host cannot run a mission's containers inside the run container — nested amd64 "
    "container failed to start: rosetta error: failed to open elf. enable Rosetta for x86/amd64 "
    "emulation in Docker Desktop. To bypass this check on a host you know is fine, set "
    "XORCISE_NESTED_CONTAINER_CHECK=skip"
)


@pytest.fixture
def refusing_server(monkeypatch):
    """A server whose run-create spine refuses because the host cannot nest containers."""

    def _refuse(**_kw):
        raise NestedContainersUnavailableError(MESSAGE)

    monkeypatch.setattr("xorcise.core.rest.routers.runs.create_run_spine", _refuse)
    return TestClient(build_rest_app(), raise_server_exceptions=False)


def test_refusal_is_a_503_not_an_unhandled_500(refusing_server, migrated_home) -> None:
    resp = refusing_server.post("/api/runs", json={"agent": "a1", "mission": "c1"})
    assert resp.status_code == 503, (
        "an unhandled ContractError surfaces as 500 with a generic body — the handler must "
        "classify this explicitly"
    )


def test_the_diagnosis_and_the_fix_both_survive_the_hop(refusing_server, migrated_home) -> None:
    """The three things the operator needs: what happened, how to fix it, how to bypass it."""
    detail = refusing_server.post("/api/runs", json={"agent": "a1", "mission": "c1"}).json()[
        "detail"
    ]
    assert "cannot run a mission's containers inside the run container" in detail
    assert "enable Rosetta" in detail
    assert "XORCISE_NESTED_CONTAINER_CHECK=skip" in detail


def test_the_body_is_json_so_the_cli_can_render_it(refusing_server, migrated_home) -> None:
    """The CLI reads `detail` out of a JSON body; a text/plain 500 would print the bare status
    and drop the message entirely."""
    resp = refusing_server.post("/api/runs", json={"agent": "a1", "mission": "c1"})
    assert resp.headers["content-type"].startswith("application/json")
    assert isinstance(resp.json().get("detail"), str)
