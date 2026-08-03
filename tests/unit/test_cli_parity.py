"""CLI/GUI feature parity — the commands that close the GUI-only gaps.

Every command here is a thin RestClient wrapper over an endpoint the Settings / Catalog / Results
GUI already calls: judge + terrain live tests, terrain-model + network setters, catalog
connect/disconnect, `xorcise system` (GET /system), `mission show` (the manifest) and the
per-agent `xorcise leaderboard` roll-up. The tests pin the endpoint each command hits (the
parity contract) plus the rendered/JSON output.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401 — registers commands/callback on the shared app
from xorcise.core.cli._shared import app
from xorcise.core.cli.commands.results import summarize_by_agent
from xorcise.core.cli.rest_client import RestClient

pytestmark = pytest.mark.unit

runner = CliRunner()

_CONFIG_VIEW: dict[str, Any] = {
    "judge": {
        "configured": True,
        "base_url": "http://h/v1",
        "model_name": "judge-m",
        "key_hint": "…cdef",
        "timeout_seconds": 120.0,
        "transcript_max_tokens": 256000,
        "tokenizer": "o200k_base",
    },
    "terrain": {
        "configured": True,
        "uses_judge_default": False,
        "base_url": "http://t/v1",
        "model_name": "terrain-m",
        "key_hint": "…9999",
        "transcript_max_tokens": 8000,
    },
    "default_budget_seconds": 3600,
    "catalog": {"connected": True, "url": "https://catalog.xorcise.ai"},
    "network": {"headscale_url": "http://hs:8080", "advertise_host": "10.0.0.5"},
}


# ── config: show folds terrain + catalog + network ────────────────────────────


def test_config_show_renders_terrain_catalog_and_network(monkeypatch):
    monkeypatch.setattr(RestClient, "get", lambda self, path: _CONFIG_VIEW)
    # Pinned so this test does not read the developer's real ~/.xorcise/config.toml to decide
    # which network verdict to print. The verdict itself is covered by the two tests below.
    monkeypatch.setattr("xorcise.core.cli.commands.config._managed_headscale_url", lambda: "")
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    out = result.stdout
    assert "terrain-m" in out and "Custom override" in out
    assert "Mission library" in out and "Enabled" in out
    assert "https://catalog.xorcise.ai" in out
    assert "http://hs:8080" in out and "10.0.0.5" in out


def test_config_show_calls_our_own_headscale_local_not_distributed(monkeypatch):
    """A healthy single-host install must NOT read as a remote deployment.

    `xorcise up` WRITES headscale_url + advertise_host for the Headscale it provisions locally,
    so the old `bool(headscale_url or advertise_host)` test labelled every ordinary install
    "Distributed" — and then offered to clear the very values `up` depends on. The discriminator
    is provision.managed_url: is this the URL our own managed block wrote?
    """
    monkeypatch.setattr(RestClient, "get", lambda self, path: _CONFIG_VIEW)
    monkeypatch.setattr(
        "xorcise.core.cli.commands.config._managed_headscale_url", lambda: "http://hs:8080"
    )
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    out = result.stdout
    assert "Local" in out
    assert "experimental" not in out.lower()
    # The address is still reported — this is about the verdict, not about hiding the value.
    assert "http://hs:8080" in out
    # And no advice to clear a setting the local install needs.
    assert "back to local" not in out


def test_config_show_flags_a_headscale_we_did_not_provision(monkeypatch):
    # The inverse: an operator-chosen control plane IS the experimental case, and the only way
    # back is a CLI command — so name it next to the readout.
    monkeypatch.setattr(RestClient, "get", lambda self, path: _CONFIG_VIEW)
    monkeypatch.setattr("xorcise.core.cli.commands.config._managed_headscale_url", lambda: "")
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    out = result.stdout
    assert "Remote control plane" in out and "experimental" in out
    assert "back to local" in out


def test_config_show_json_emits_raw_view(monkeypatch):
    import json

    monkeypatch.setattr(RestClient, "get", lambda self, path: _CONFIG_VIEW)
    result = runner.invoke(app, ["config", "show", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == _CONFIG_VIEW


def test_config_show_tolerates_a_view_without_the_new_blocks(monkeypatch):
    # An older/partial view must still render (the GUI blocks are read defensively).
    monkeypatch.setattr(
        RestClient,
        "get",
        lambda self, path: {"judge": {"configured": False}, "default_budget_seconds": 60},
    )
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "Not configured" in result.stdout


# ── config: judge + terrain live tests ────────────────────────────────────────


def test_config_test_posts_judge_live_test(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_post(self, path, json, timeout=None):  # noqa: ANN001, ANN202 — test stub
        seen["path"], seen["timeout"] = path, timeout
        return {"ok": True, "status": "ok", "model_name": "judge-m"}

    monkeypatch.setattr(RestClient, "post", fake_post)
    result = runner.invoke(app, ["config", "test"])
    assert result.exit_code == 0
    assert seen["path"] == "/config/model/test"
    # A live model call must not be cut off by the 5s control-call default.
    assert seen["timeout"] and seen["timeout"] > 60
    assert "judge model ok" in result.stdout and "judge-m" in result.stdout


def test_config_test_exits_nonzero_when_the_model_fails(monkeypatch):
    monkeypatch.setattr(
        RestClient,
        "post",
        lambda self, path, json, timeout=None: {
            "ok": False,
            "status": "error",
            "message": "401 unauthorized",
        },
    )
    result = runner.invoke(app, ["config", "test"])
    assert result.exit_code == 1
    # A failing live test is an error condition — it rides stderr, with a pointer.
    assert "401 unauthorized" in result.stderr
    assert "xorcise config show" in result.stderr


def test_config_test_terrain_posts_terrain_live_test(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_post(self, path, json, timeout=None):  # noqa: ANN001, ANN202 — test stub
        seen["path"] = path
        return {"ok": True, "status": "ok", "model_name": "terrain-m"}

    monkeypatch.setattr(RestClient, "post", fake_post)
    result = runner.invoke(app, ["config", "test-terrain"])
    assert result.exit_code == 0
    assert seen["path"] == "/config/terrain-model/test"
    assert "terrain model ok" in result.stdout


def test_config_test_json_emits_raw_result_and_keeps_the_exit_code(monkeypatch):
    import json

    payload = {"ok": False, "status": "not_configured", "message": "no key"}
    monkeypatch.setattr(RestClient, "post", lambda self, path, json, timeout=None: payload)
    result = runner.invoke(app, ["config", "test", "--json"])
    # Same gate in both output modes: the body is emitted, the failure still exits non-zero.
    assert result.exit_code == 1
    assert json.loads(result.stdout) == payload


def test_config_test_json_exits_zero_when_ok(monkeypatch):
    monkeypatch.setattr(
        RestClient,
        "post",
        lambda self, path, json, timeout=None: {"ok": True, "status": "ok", "model_name": "m"},
    )
    result = runner.invoke(app, ["config", "test", "--json"])
    assert result.exit_code == 0


# ── config: terrain-model + network setters ───────────────────────────────────


def test_config_set_terrain_model_puts_only_given_fields(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_put(self, path, json):  # noqa: ANN001, ANN202 — test stub
        seen["path"], seen["json"] = path, json
        return _CONFIG_VIEW

    monkeypatch.setattr(RestClient, "put", fake_put)
    result = runner.invoke(
        app,
        ["config", "set-terrain-model", "--name", "terrain-m", "--transcript-max-tokens", "8000"],
    )
    assert result.exit_code == 0
    assert seen["path"] == "/config/terrain-model"
    assert seen["json"] == {
        "key": None,
        "base_url": None,
        "model_name": "terrain-m",
        "transcript_max_tokens": 8000,
    }
    assert "terrain-m" in result.stdout and "custom override" in result.stdout


def test_config_set_terrain_model_empty_string_clears_the_override(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_put(self, path, json):  # noqa: ANN001, ANN202 — test stub
        seen["json"] = json
        return {
            **_CONFIG_VIEW,
            "terrain": {"configured": True, "uses_judge_default": True, "model_name": "judge-m"},
        }

    monkeypatch.setattr(RestClient, "put", fake_put)
    result = runner.invoke(app, ["config", "set-terrain-model", "--name", ""])
    assert result.exit_code == 0
    assert seen["json"]["model_name"] == ""  # "" = unset → falls back to the judge
    assert "judge default" in result.stdout


def test_config_set_network_puts_addresses_and_warns_about_restart(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_put(self, path, json):  # noqa: ANN001, ANN202 — test stub
        seen["path"], seen["json"] = path, json
        return _CONFIG_VIEW

    monkeypatch.setattr(RestClient, "put", fake_put)
    result = runner.invoke(
        app,
        ["config", "set-network", "--headscale-url", "http://hs:8080", "--advertise-host", "h1"],
    )
    assert result.exit_code == 0
    assert seen["path"] == "/config/network"
    assert seen["json"] == {"headscale_url": "http://hs:8080", "advertise_host": "h1"}
    assert "applies on the next" in result.stdout


# ── catalog connect / disconnect ──────────────────────────────────────────────


def test_catalog_connect_puts_connected_true(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_put(self, path, json):  # noqa: ANN001, ANN202 — test stub
        seen["path"], seen["json"] = path, json
        return _CONFIG_VIEW

    monkeypatch.setattr(RestClient, "put", fake_put)
    # connect is state-aware now: it reads the current setting, then live-checks.
    monkeypatch.setattr(
        RestClient,
        "get",
        lambda self, path, timeout=None: {"catalog": {"connected": False, "url": None}},
    )
    monkeypatch.setattr(
        RestClient, "get_or_none", lambda self, path, timeout=None: {"state": "connected"}
    )
    result = runner.invoke(app, ["catalog", "connect"])
    assert result.exit_code == 0
    assert seen["path"] == "/config/catalog"
    assert seen["json"] == {"connected": True}
    assert "library enabled" in result.stdout


def test_catalog_disconnect_puts_connected_false(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_put(self, path, json):  # noqa: ANN001, ANN202 — test stub
        seen["json"] = json
        return {**_CONFIG_VIEW, "catalog": {"connected": False, "url": None}}

    monkeypatch.setattr(RestClient, "put", fake_put)
    monkeypatch.setattr(
        RestClient,
        "get",
        lambda self, path, timeout=None: {"catalog": {"connected": True, "url": "https://x"}},
    )
    result = runner.invoke(app, ["catalog", "disconnect"])
    assert result.exit_code == 0
    assert seen["json"] == {"connected": False}
    assert "library disabled" in result.stdout


# ── xorcise system ────────────────────────────────────────────────────────────

_SYSTEM_INFO: dict[str, Any] = {
    "role": "all",
    "planes": [
        {"name": "rest", "ok": True, "detail": "ok", "location": "127.0.0.1:8000"},
        {"name": "docker", "ok": False, "detail": "unreachable", "location": "local daemon"},
    ],
    "db_schema": "head",
    "catalog": {"state": "connected", "message": None, "last_sync": None},
    "remotes": [],
    "home": "/home/u/.xorcise",
    "db_url": "sqlite:////home/u/.xorcise/xorcise.db",
    "topology": "local",
}


def test_system_renders_planes_table_and_environment(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_get(self, path):  # noqa: ANN001, ANN202 — test stub
        seen["path"] = path
        return _SYSTEM_INFO

    monkeypatch.setattr(RestClient, "get", fake_get)
    result = runner.invoke(app, ["system"])
    assert result.exit_code == 0
    assert seen["path"] == "/system"
    out = result.stdout
    assert "REST API" in out and "Docker" in out and "unreachable" in out
    assert "127.0.0.1:8000" in out
    assert "mission library: enabled" in out
    # Deployment internals are progressive-disclosure: default view hides them.
    for internal in ("sqlite:", "/home/u/.xorcise", "head", "topology"):
        assert internal not in out


def test_system_verbose_reveals_deployment_internals(monkeypatch):
    monkeypatch.setattr(RestClient, "get", lambda self, path: _SYSTEM_INFO)
    result = runner.invoke(app, ["system", "--verbose"])
    assert result.exit_code == 0
    out = result.stdout
    assert "head" in out and "local" in out
    assert "/home/u/.xorcise" in out
    assert "sqlite:" in out


def test_system_json_emits_raw_view(monkeypatch):
    import json

    monkeypatch.setattr(RestClient, "get", lambda self, path: _SYSTEM_INFO)
    result = runner.invoke(app, ["system", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == _SYSTEM_INFO


def test_system_is_registered_on_the_root_app():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "system" in result.stdout
    assert "leaderboard" in result.stdout


# ── mission show ────────────────────────────────────────────────────────────

_MANIFEST: dict[str, Any] = {
    "schema_version": "2.0",
    "metadata": {
        "mission_id": "sqli-login",
        "name": "SQLi Login",
        "summary": "A login form with an injectable query.",
        "objective": "Recover the admin flag.",
        "proficiency": "easy",
        "specialty": "web",
        "type": "lab",
        "skills": ["sqli", "recon"],
        "technologies": ["nginx"],
    },
    "rubric": [{"id": "r1", "text": "found it"}],
    "checks": [],
    "attachments": [],
}


def _mission_show_get(seen: dict[str, Any]):  # noqa: ANN202 — test stub factory
    """`mission show` now resolves the id against /missions before the manifest read."""

    def fake_get(self, path):  # noqa: ANN001, ANN202 — test stub
        if path == "/missions":
            return [{"mission_id": "sqli-login", "name": "SQLi Login", "installed": True}]
        seen["path"] = path
        return _MANIFEST

    return fake_get


def test_mission_show_reads_the_manifest_endpoint(monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(RestClient, "get", _mission_show_get(seen))
    result = runner.invoke(app, ["mission", "show", "sqli-login"])
    assert result.exit_code == 0
    assert seen["path"] == "/missions/sqli-login/manifest"
    out = result.stdout
    assert "SQLi Login" in out and "sqli-login" in out
    assert "Lab" in out and "Easy" in out and "Web" in out
    assert "Recover the admin flag." in out
    assert "1 criteria scored by the judge model" in out


def test_mission_show_json_emits_the_raw_manifest(monkeypatch):
    import json

    monkeypatch.setattr(RestClient, "get", _mission_show_get({}))
    result = runner.invoke(app, ["mission", "show", "sqli-login", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == _MANIFEST


# ── xorcise leaderboard ───────────────────────────────────────────────────────


def _run(
    run_id: str, agent_id: str, trigger: str, when: str = "2026-07-01T10:00:00"
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "agent_id": agent_id,
        "mission": "sqli",
        "state": "terminal",
        "created_at": when,
        "completed_at": when,
        "terminal_trigger": trigger,
    }


def _wire(
    monkeypatch,
    runs: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    agents: list[dict[str, Any]],
) -> None:
    def fake_get(self, path):  # noqa: ANN001, ANN202 — test stub
        if path == "/runs":
            return runs
        if path == "/agents":
            return agents
        run_id = path.split("/")[2]
        return results[run_id]

    monkeypatch.setattr(RestClient, "get", fake_get)


def test_leaderboard_ranks_agents_by_average_overall(monkeypatch):
    _wire(
        monkeypatch,
        runs=[
            _run("r1", "a1", "done", "2026-07-01T10:00:00"),
            _run("r2", "a1", "done", "2026-07-02T10:00:00"),
            _run("r3", "a2", "done", "2026-07-03T10:00:00"),
        ],
        results={
            "r1": {"grade": {"overall": 0.4}, "partial": False},
            "r2": {"grade": {"overall": 0.6}, "partial": False},
            "r3": {"grade": {"overall": 0.9}, "partial": False},
        },
        agents=[{"id": "a1", "name": "alpha"}, {"id": "a2", "name": "beta"}],
    )
    result = runner.invoke(app, ["leaderboard"])
    assert result.exit_code == 0
    out = result.stdout
    # beta (0.90) outranks alpha (0.50)
    assert out.index("beta") < out.index("alpha")
    assert "0.90" in out and "0.50" in out


def test_leaderboard_excludes_partial_runs_from_the_score_but_counts_them(monkeypatch):
    _wire(
        monkeypatch,
        runs=[_run("r1", "a1", "done"), _run("r2", "a1", "timeout")],
        results={
            "r1": {"grade": {"overall": 0.8}, "partial": False},
            "r2": {"grade": {"overall": 0.1}, "partial": True},
        },
        agents=[{"id": "a1", "name": "alpha"}],
    )
    result = runner.invoke(app, ["leaderboard", "--json"])
    assert result.exit_code == 0
    import json

    (row,) = json.loads(result.stdout)
    assert row["runs"] == 2 and row["scored"] == 1
    assert row["avg_overall"] == 0.8 and row["best_overall"] == 0.8
    assert row["completion_rate"] == 0.5 and row["partial_rate"] == 0.5


def test_leaderboard_tolerates_a_terminal_but_ungraded_run(monkeypatch):
    # GET /runs/{id}/result 202s with {"status": "grading"} while grading is async.
    _wire(
        monkeypatch,
        runs=[_run("r1", "a1", "done")],
        results={"r1": {"run_id": "r1", "status": "grading"}},
        agents=[{"id": "a1", "name": "alpha"}],
    )
    result = runner.invoke(app, ["leaderboard"])
    assert result.exit_code == 0
    assert "alpha" in result.stdout


def test_leaderboard_skips_non_terminal_runs(monkeypatch):
    active = {**_run("r1", "a1", "done"), "state": "active"}
    _wire(monkeypatch, runs=[active], results={}, agents=[])
    result = runner.invoke(app, ["leaderboard"])
    assert result.exit_code == 0
    assert "no finished runs yet" in result.stdout


def test_summarize_by_agent_sinks_unscored_agents_and_falls_back_to_the_id():
    rows: list[dict[str, Any]] = [
        {
            "agent_id": "a1",
            "overall": None,
            "partial": True,
            "completed": False,
            "when": "2026-07-01T10:00:00",
        },
        {
            "agent_id": "a2deadbeefcafe",
            "overall": 0.3,
            "partial": False,
            "completed": True,
            "when": "2026-07-02T10:00:00",
        },
    ]
    scored, unscored = summarize_by_agent(rows, {"a1": "alpha"})
    assert scored["agent_name"] == "a2deadbe"  # no registered name → short id (GUI parity)
    assert scored["avg_overall"] == 0.3
    assert unscored["agent_name"] == "alpha" and unscored["avg_overall"] is None
