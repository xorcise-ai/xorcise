"""GET /runs/{id}/alive and the run's watch token (#172).

The join script's self-reaper polls run-control for the run ending. It used to poll /mission with
the full run-control bearer on every process command line it spawned; it now polls /alive with a
watch token that authenticates nothing else. These tests pin both halves of that separation: the
watch token opens /alive and only /alive, and the bearer does not open /alive."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from xorcise.core import runs
from xorcise.core.rest.routers import runcontrol
from xorcise.core.runs.observed import SqliteObservedFactsStore

pytestmark = pytest.mark.unit


def _make_run(run_control_key: str = "rk-bearer") -> str:
    return runs.create_run(
        agent_id="a1", mission="c", run_control_key=run_control_key, join_key="tskey-secret"
    ).run_id


def _alive_status(rid: str, authorization: str | None) -> int:
    try:
        return runcontrol.get_alive(run_id=rid, authorization=authorization).status_code
    except HTTPException as exc:
        return exc.status_code


def test_watch_token_is_derived_per_run_and_is_not_the_bearer(migrated_home):
    a, b = _make_run(), _make_run()
    token_a = runs.watch_token(a)
    assert token_a and token_a != "rk-bearer"
    assert runs.watch_token(a) == token_a  # stable: the served script and the probe agree
    assert runs.watch_token(b) != token_a  # same bearer, different run, different token
    assert runs.watch_token("no-such-run") is None


def test_alive_answers_204_for_the_watch_token_while_the_run_is_live(migrated_home):
    rid = _make_run()
    assert _alive_status(rid, f"Bearer {runs.watch_token(rid)}") == 204


def test_alive_answers_409_once_the_run_has_ended(migrated_home):
    rid = _make_run()
    runs.mark_terminal(rid, "done", datetime.now(UTC))
    assert _alive_status(rid, f"Bearer {runs.watch_token(rid)}") == 409


def test_alive_answers_409_for_a_run_that_no_longer_exists(migrated_home):
    # The token derives from the run's row, so for a deleted run a 401 would be permanent — and the
    # reapers stop only on 409, so they would hold tailscaled until their hard cap.
    rid = _make_run()
    token = runs.watch_token(rid)
    runs.delete_run(rid)
    assert _alive_status(rid, f"Bearer {token}") == 409
    assert _alive_status("no-such-run", "Bearer anything") == 409


def test_alive_refuses_the_bearer_and_any_other_credential(migrated_home):
    rid, other = _make_run(), _make_run()
    assert _alive_status(rid, "Bearer rk-bearer") == 401  # the bearer is not a watch token
    assert _alive_status(rid, None) == 401
    assert _alive_status(rid, "Bearer ") == 401
    assert _alive_status(rid, f"Bearer {runs.watch_token(other)}") == 401  # another run's token


def test_the_watch_token_opens_no_other_endpoint(migrated_home, monkeypatch):
    # The point of the token: if it leaks it must not act as the agent. Every bearer-gated route
    # authenticates through require_run, so refusing it there refuses it everywhere.
    rid = _make_run()
    with pytest.raises(HTTPException) as ei:
        runcontrol.require_run(rid, f"Bearer {runs.watch_token(rid)}")
    assert ei.value.status_code == 401
    assert runcontrol.require_run(rid, "Bearer rk-bearer") == rid  # the bearer still works there


def test_alive_does_not_record_that_the_agent_read_its_brief(migrated_home):
    # The reaper used to poll /mission, which records the 'prompt fetched' fact — so a run whose
    # agent never read its brief still lit rc:prompt 15 seconds after the join.
    rid = _make_run()
    runcontrol.get_alive(run_id=rid, authorization=f"Bearer {runs.watch_token(rid)}")
    facts = SqliteObservedFactsStore().list_for_run(rid)
    assert not [f for f in facts if f.kind == "runcontrol-lifecycle" and f.name == "prompt"]


def test_served_join_script_gives_the_reaper_the_watch_token_not_the_bearer(
    migrated_home, monkeypatch
):
    settings = SimpleNamespace(
        headscale_ca_cert="",
        headscale_url="https://headscale.local:8443",
        headscale_advertise_host="",
        headscale_port=8443,
        host="localhost",
        headscale_host_alias="",
    )
    monkeypatch.setattr(runcontrol, "get_settings", lambda: settings)
    rid = _make_run()
    request = Request(
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
    resp = runcontrol.get_join_script(request=request, run_id=rid, authorization="Bearer rk-bearer")
    body = bytes(resp.body).decode()
    assert f'WATCH_KEY="{runs.watch_token(rid)}"' in body
    assert '"$base/alive"' in body  # both reapers poll the liveness probe
    assert '"$base/mission"' not in body
