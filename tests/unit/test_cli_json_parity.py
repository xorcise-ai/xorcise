"""`--json` parity for the commands that lacked it.

All three defects here share a shape: a command that answers a human correctly but
answers a SCRIPT badly, or not at all. `--json` is the machine contract — a command that
exits 0 must emit parseable JSON on every success path, and a command that fails must say
what to do next.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401 — registers commands on the shared app
from xorcise.core.cli._shared import app

pytestmark = pytest.mark.unit

runner = CliRunner()


# --- role: the group had no machine contract at all --------------------------


def test_role_list_json_is_parseable_and_flags_experimental():
    result = runner.invoke(app, ["role", "list", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert {r["key"] for r in rows} >= {"all", "control", "runner", "headscale", "collector"}
    by_key = {r["key"]: r for r in rows}
    # `experimental` is a boolean as well as a display string: a CI gate should never have
    # to string-match "Experimental" to decide whether a role is safe to deploy.
    assert by_key["all"]["experimental"] is False
    assert by_key["control"]["experimental"] is True
    assert all(r["label"] and r["purpose"] for r in rows)


def test_role_show_json_names_where_the_key_came_from(monkeypatch):
    # This command reads the SHELL's env, not a running server, so the JSON says so —
    # otherwise a script cannot tell "explicitly set" from "defaulted".
    monkeypatch.delenv("XORCISE_ROLE", raising=False)
    result = runner.invoke(app, ["role", "show", "--json"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["key"] == "all"
    assert body["experimental"] is False
    assert body["source"] == "default"

    monkeypatch.setenv("XORCISE_ROLE", "collector")
    body = json.loads(runner.invoke(app, ["role", "show", "--json"]).stdout)
    assert body["key"] == "collector"
    assert body["experimental"] is True
    assert body["source"] == "XORCISE_ROLE"


def test_role_human_output_still_renders():
    # Parity must not be bought by degrading the human path.
    for argv in (["role", "list"], ["role", "show"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0
        assert "Experimental" in result.stdout or "Ready" in result.stdout


def test_remote_list_json_is_an_empty_list_not_prose():
    # `remote` is a hidden forward-compat stub, but a stub still owes a STABLE machine
    # contract: `[]` parses and iterates, so a script written today keeps working when
    # real remotes land. Prose would have to be rewritten on both sides.
    result = runner.invoke(app, ["remote", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
