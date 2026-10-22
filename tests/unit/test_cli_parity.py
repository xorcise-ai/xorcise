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


def test_system_warns_about_environments_a_teardown_left_up(monkeypatch):
    """#173: finished runs whose container or tailnet nodes are still up after a failed teardown
    are counted, so a plane that is down gets noticed. Nothing is printed when there are none."""
    info = {
        **_SYSTEM_INFO,
        "releases": {
            "pending": 2,
            "oldest_ended_at": "2026-10-14T12:00:00Z",
            "oldest_error": "tailnet: Headscale control plane 'headscale' is not reachable",
        },
    }
    monkeypatch.setattr(RestClient, "get", lambda self, path: info)
    out = runner.invoke(app, ["system"]).stdout
    assert "2 finished run(s) still hold their container or tailnet nodes" in out
    assert "retried automatically" in out
    assert "Headscale control plane 'headscale' is not reachable" in out

    for quiet in ({**_SYSTEM_INFO, "releases": {"pending": 0}}, _SYSTEM_INFO):
        monkeypatch.setattr(RestClient, "get", lambda self, path, quiet=quiet: quiet)
        assert "releases:" not in runner.invoke(app, ["system"]).stdout


@pytest.mark.parametrize(
    ("view", "expected", "absent"),
    [
        (
            {"state": "failed", "error": "runs: Headscale control plane is not reachable"},
            ["boot reconcile failed", "Headscale control plane is not reachable", "next start"],
            [],
        ),
        (
            {
                "state": "done",
                "adopted": 1,
                "aborted": 2,
                "released": 1,
                "release_pending": ["d" * 32],
                "failed": ["e" * 32],
            },
            [
                "1 still running (picked back up)",
                "2 closed out as crashed (not graded)",
                "1 unfinished deploy(s) cleaned up",
                "1 unfinished deploy(s) could not be torn down",
                "closed as deploy failed (not graded); the teardown is retried automatically",
                "dddddddd",
                "1 run(s) could not be checked",
                "eeeeeeee",
            ],
            [],
        ),
        # A clean start, or one still in progress, is not worth a line in the default view.
        ({"state": "done"}, [], ["boot reconcile"]),
        ({"state": "running"}, [], ["boot reconcile"]),
    ],
)
def test_system_reports_what_the_boot_reconcile_did(monkeypatch, view, expected, absent):
    """#173: what the server recovered at start used to be a line in serve.log, and a reconcile
    that failed outright said nothing at all."""
    info = {**_SYSTEM_INFO, "boot_reconcile": view}
    monkeypatch.setattr(RestClient, "get", lambda self, path: info)
    out = " ".join(runner.invoke(app, ["system"]).stdout.split())
    for text in expected:
        assert text in out
    for text in absent:
        assert text not in out


def test_system_verbose_says_when_the_boot_reconcile_had_nothing_to_do(monkeypatch):
    info = {**_SYSTEM_INFO, "boot_reconcile": {"state": "done"}}
    monkeypatch.setattr(RestClient, "get", lambda self, path: info)
    assert "boot reconcile: nothing to recover" in runner.invoke(app, ["system", "-v"]).stdout


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
#
# The leaderboard is the server's summary (GET /results/summary?by=agent, reporting.scores), named
# and ranked. How each run counts is decided once, server-side, and tested there
# (tests/unit/test_results_scores.py); these pin what the CLI does with it.


def _summary(agent_id: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "agent_id": agent_id,
        "runs": 1,
        "scored": 1,
        "timed_out": 0,
        "no_judge": 0,
        "stopped": 0,
        "infra_failed": 0,
        "grading": 0,
        "median": 0.5,
        "best": 0.5,
        "last_run": "2026-07-01T10:00:00",
    }
    row.update(over)
    return row


def _wire(monkeypatch, summaries: list[dict[str, Any]], agents: list[dict[str, Any]]) -> list[str]:
    calls: list[str] = []

    def fake_get(self, path):  # noqa: ANN001, ANN202 — test stub
        calls.append(path)
        if path == "/results/summary?by=agent":
            return summaries
        if path == "/agents":
            return agents
        raise AssertionError(f"unexpected request {path}")

    monkeypatch.setattr(RestClient, "get", fake_get)
    return calls


def test_leaderboard_reads_the_servers_summary_not_every_runs_result(monkeypatch):
    calls = _wire(monkeypatch, [_summary("a1")], [{"id": "a1", "name": "alpha"}])
    result = runner.invoke(app, ["leaderboard"])
    assert result.exit_code == 0
    assert calls == ["/results/summary?by=agent", "/agents"]


def test_leaderboard_ranks_agents_by_median(monkeypatch):
    _wire(
        monkeypatch,
        [_summary("a1", median=0.5), _summary("a2", median=0.9), _summary("a3", median=None)],
        [
            {"id": "a1", "name": "alpha"},
            {"id": "a2", "name": "beta"},
            {"id": "a3", "name": "gamma"},
        ],
    )
    result = runner.invoke(app, ["leaderboard"])
    assert result.exit_code == 0
    out = result.stdout
    # beta (0.90) outranks alpha (0.50); gamma, with no scored run, sinks.
    assert out.index("beta") < out.index("alpha") < out.index("gamma")
    assert "Median" in out and "90%" in out


def test_leaderboard_json_is_the_summary_named_and_ranked(monkeypatch):
    import json

    _wire(
        monkeypatch,
        [_summary("a2deadbeefcafe", median=0.3), _summary("a1", median=0.8, timed_out=1)],
        [{"id": "a1", "name": "alpha"}],
    )
    result = runner.invoke(app, ["leaderboard", "--json"])
    assert result.exit_code == 0
    first, second = json.loads(result.stdout)
    assert (first["agent_name"], first["median"], first["timed_out"]) == ("alpha", 0.8, 1)
    # No registered name: the short id, as the GUI shows it.
    assert second["agent_name"] == "a2deadbe"


def test_leaderboard_says_which_runs_are_in_the_median_and_which_are_not(monkeypatch):
    _wire(
        monkeypatch,
        [_summary("a1", timed_out=1, no_judge=2, stopped=1, infra_failed=1)],
        [{"id": "a1", "name": "alpha"}],
    )
    out = " ".join(runner.invoke(app, ["leaderboard"]).stdout.split())
    assert "'Timed out' runs ran out of their budget. They ARE in Median/Best" in out
    assert "'No judge' counts runs graded with the judge half unavailable" in out
    assert "left out of Median/Best" in out
    assert "'Stopped' counts runs an operator ended" in out
    assert "'Infra failed' counts runs cut short by an environment failure" in out


def test_leaderboard_with_no_finished_runs(monkeypatch):
    _wire(monkeypatch, [], [])
    result = runner.invoke(app, ["leaderboard"])
    assert result.exit_code == 0
    assert "no finished runs yet" in result.stdout
    assert runner.invoke(app, ["leaderboard", "--json"]).stdout.strip() == "[]"


def test_rank_sinks_unscored_agents_and_breaks_ties_on_scored_runs():
    from xorcise.core.cli.commands.results import rank

    ranked = rank(
        [
            _summary("a1", median=None, scored=0),
            _summary("a2", median=0.7, scored=1),
            _summary("a3", median=0.7, scored=4),
        ],
        {"a1": "alpha", "a2": "beta", "a3": "gamma"},
    )
    assert [r["agent_name"] for r in ranked] == ["gamma", "beta", "alpha"]


def test_leaderboard_prints_a_score_as_the_gui_does():
    """0.815 is stored just under 0.815: `.2f` printed 0.81 where the page showed 82%."""
    from xorcise.core.cli.commands.results import _score

    assert _score(0.815) == "82%"
    assert _score(0.5674999999999999) == "57%"
    assert _score(None) == "—"
