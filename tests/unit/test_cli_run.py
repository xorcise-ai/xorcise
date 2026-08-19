from __future__ import annotations

from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401  -- importing registers the command groups
from xorcise.core.cli._shared import app

runner = CliRunner()

# A full-length (>= 32 char) run id: `_resolve_id` passes it through untouched, so the
# commands make zero extra REST calls — same as the pre-revamp behavior the tests assume.
RID = "a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6"
RID8 = RID[:8]


def _create_catalog_get(self, path: str):  # noqa: ANN001, ANN202 — test stub
    """GET stub for `run create` pre-validation: exact agent name + installed mission."""
    if path == "/agents":
        return [{"id": "agent-1", "name": "a"}]
    if path == "/missions":
        return [{"mission_id": "c", "name": "c", "installed": True}]
    raise AssertionError(f"unexpected GET {path}")


def test_run_create_requires_agent_and_mission():
    result = runner.invoke(app, ["run", "create"])
    assert result.exit_code != 0


def test_run_create_passes_budget_to_server(monkeypatch):
    captured: dict[str, object] = {}

    def fake_post(self, path: str, json: dict[str, object], timeout: float | None = None):
        captured["path"] = path
        captured["json"] = json
        return {"run_id": RID}

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get", _create_catalog_get)
    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.post", fake_post)
    result = runner.invoke(
        app, ["run", "create", "--agent", "a", "--mission", "c", "--budget", "120"]
    )
    assert result.exit_code == 0
    assert captured["path"] == "/runs"
    assert captured["json"] == {"agent": "a", "mission": "c", "budget_seconds": 120}
    assert f"run {RID} created" in result.stdout


def test_run_create_omits_budget_when_unset(monkeypatch):
    seen: list[dict[str, object]] = []

    def fake_post(self, path: str, json: dict[str, object], timeout: float | None = None):
        seen.append(json)
        return {"run_id": RID}

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get", _create_catalog_get)
    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.post", fake_post)
    runner.invoke(app, ["run", "create", "--agent", "a", "--mission", "c"])
    assert "budget_seconds" not in seen[0]


def _grade_payload(
    *,
    run_id: str = RID,
    overall: float = 0.4,
    deterministic: float = 0.6,
    judge: float = 0.2,
    key_evidence: list[str] | None = None,
    major_deductions: list[str] | None = None,
    artifacts: list[str] | None = None,
    trace_ref: str | None = RID,
    hard_fails: list[str] | None = None,
    judge_breakdown: list[dict[str, object]] | None = None,
    check_breakdown: list[dict[str, object]] | None = None,
    judge_status: str = "ok",
    judge_upper: float | None = None,
    overall_upper: float | None = None,
    judge_coverage: float | None = None,
) -> dict[str, object]:
    """Build the nested {grade, conditions} payload the REST endpoint now returns."""
    return {
        "grade": {
            "run_id": run_id,
            "overall": overall,
            "breakdown": {"deterministic": deterministic, "judge": judge},
            "key_evidence": key_evidence or [],
            "major_deductions": major_deductions or [],
            "artifacts": artifacts or [],
            "trace_ref": trace_ref,
            "hard_fails": hard_fails or [],
            "judge_status": judge_status,
            "judge_detail": None,
            "judge_upper": judge_upper,
            "overall_upper": overall_upper,
            "judge_coverage": judge_coverage,
            "judge_breakdown": judge_breakdown or [],
            "check_breakdown": check_breakdown or [],
        },
        "conditions": {
            "model": None,
            "judge_model": None,
            "budget_seconds": 0,
            "sandbox_ref": None,
        },
    }


def test_run_status_renders_scores_and_hardfail(monkeypatch):
    payload = _grade_payload(
        overall=0.4,
        key_evidence=["port 22 open"],
        major_deductions=["no flag"],
        artifacts=["a1"],
        trace_ref=RID,
        hard_fails=["rooted host"],
    )
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result", lambda self, p: payload
    )
    res = runner.invoke(app, ["run", "status", RID])
    assert res.exit_code == 0
    assert "0.4" in res.stdout
    assert "HARD-FAIL" in res.stdout
    assert "rooted host" in res.stdout
    assert f"trace: {RID}" in res.stdout
    # model not disclosed when model is None
    assert "model not disclosed" in res.stdout


def test_run_status_low_score_no_hardfails_omits_hardfail_marker(monkeypatch):
    """A low overall score with NO hard_fails must NOT print the hard-fail marker."""
    payload = _grade_payload(
        overall=0.1,
        deterministic=0.1,
        judge=0.1,
        major_deductions=["missed everything"],
        trace_ref=None,
    )
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result", lambda self, p: payload
    )
    res = runner.invoke(app, ["run", "status", RID])
    assert res.exit_code == 0
    assert "0.1" in res.stdout
    assert "HARD-FAIL" not in res.stdout
    # null trace_ref must be guarded like the other optional fields — no "trace: None" leak
    assert "None" not in res.stdout
    # model not disclosed when model is None
    assert "model not disclosed" in res.stdout


def test_run_status_renders_conditions_when_model_set(monkeypatch):
    """When model is set, it is shown; budget + sandbox are rendered."""
    payload = _grade_payload(overall=0.9)
    payload["conditions"] = {
        "model": "gpt-4o",
        "judge_model": "claude-3-5",
        "budget_seconds": 300,
        "sandbox_ref": "xorcise/mission-c1:0",
    }
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result", lambda self, p: payload
    )
    res = runner.invoke(app, ["run", "status", RID])
    assert res.exit_code == 0
    assert "gpt-4o" in res.stdout
    assert "claude-3-5" in res.stdout
    assert "300s" in res.stdout
    assert "xorcise/mission-c1:0" in res.stdout


def test_run_status_shows_partial_banner_on_timed_out_result(monkeypatch):
    """Run status shows a PARTIAL banner when partial=True in the payload."""
    payload = _grade_payload(overall=0.4)
    payload["partial"] = True
    payload["partial_trigger"] = "timeout"
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result", lambda self, p: payload
    )
    res = runner.invoke(app, ["run", "status", RID])
    assert res.exit_code == 0
    assert "PARTIAL" in res.stdout
    assert "timeout" in res.stdout


def test_run_status_no_partial_banner_on_clean_result(monkeypatch):
    """Run status must NOT show any partial marker for a clean (done) result.

    No-false-positive guard: the PARTIAL marker must be absent when partial=False.
    """
    payload = _grade_payload(overall=0.9)
    payload["partial"] = False
    payload["partial_trigger"] = None
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result", lambda self, p: payload
    )
    res = runner.invoke(app, ["run", "status", RID])
    assert res.exit_code == 0
    assert "PARTIAL" not in res.stdout


def test_run_status_json_dumps_full_result(monkeypatch):
    """--json emits the full result envelope as parseable JSON."""
    import json as _json

    payload = _grade_payload(overall=0.9, deterministic=0.9, judge=0.9)
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result", lambda self, p: payload
    )
    res = runner.invoke(app, ["run", "status", RID, "--json"])
    assert res.exit_code == 0
    parsed = _json.loads(res.stdout)
    assert parsed["grade"]["overall"] == 0.9


def test_run_status_verbose_renders_breakdowns(monkeypatch):
    """--verbose renders the per-check deterministic + per-criterion judge breakdown."""
    payload = _grade_payload(
        overall=1.0,
        deterministic=1.0,
        judge=1.0,
        check_breakdown=[
            {
                "id": "flag-correct",
                "source": "artifacts",
                "ref": "flag",
                "op": "matches_format",
                "value": "XORCISE{x}",
                "passed": True,
                "weight": 0.7,
            }
        ],
        judge_breakdown=[
            {
                "criterion_id": "auth-bypass",
                "text": "Bypassed authentication.",
                "weight": 0.4,
                "score": 1.0,
                "reason": "demonstrated the bypass",
            }
        ],
    )
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result", lambda self, p: payload
    )
    res = runner.invoke(app, ["run", "status", RID, "--verbose"])
    assert res.exit_code == 0
    assert "flag-correct" in res.stdout
    assert "auth-bypass" in res.stdout
    assert "demonstrated the bypass" in res.stdout


def test_run_status_default_omits_breakdown(monkeypatch):
    """Without --verbose the per-criterion detail stays hidden (compact default)."""
    payload = _grade_payload(
        overall=1.0,
        judge_breakdown=[
            {"criterion_id": "auth-bypass", "text": "t", "weight": 0.4, "score": 1.0, "reason": "r"}
        ],
    )
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result", lambda self, p: payload
    )
    res = runner.invoke(app, ["run", "status", RID])
    assert res.exit_code == 0
    assert "auth-bypass" not in res.stdout


def test_run_status_discloses_partial_judge_ranges_and_coverage(monkeypatch):
    payload = _grade_payload(
        overall=0.55,
        overall_upper=0.85,
        judge=0.1,
        judge_upper=0.7,
        judge_status="partial",
        judge_coverage=0.4,
    )
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result", lambda self, p: payload
    )
    res = runner.invoke(app, ["run", "status", RID])
    assert res.exit_code == 0
    assert "overall=0.55–0.85" in res.stdout
    assert "judge=0.10–0.70" in res.stdout
    assert "40% of rubric weight scored" in res.stdout


def test_run_status_grading_in_progress(monkeypatch):
    """The 202 grading signal renders a friendly line and exits 3 (in progress, not failure)."""
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result",
        lambda self, p: {"run_id": RID, "status": "grading"},
    )
    res = runner.invoke(app, ["run", "status", RID])
    assert res.exit_code == 3
    assert "grading" in res.stdout.lower()
    # the human message shows the short 8-char id, not the full one
    assert RID8 in res.stdout
    # must not crash trying to read grade/conditions off the grading signal
    assert "KeyError" not in res.stdout


def test_run_status_json_is_parseable_even_while_grading(monkeypatch):
    """--json must NEVER emit prose: during the grading window it emits the raw envelope."""
    import json as _json

    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result",
        lambda self, p: {"run_id": RID, "status": "grading"},
    )
    res = runner.invoke(app, ["run", "status", RID, "--json"])
    assert res.exit_code == 0
    assert _json.loads(res.stdout) == {"run_id": RID, "status": "grading"}


def test_run_terminate_posts_to_terminate_endpoint(monkeypatch):
    """`run terminate` calls POST /runs/<id>/terminate and shows the new state."""
    captured: dict[str, object] = {}

    def fake_post(self, path: str, json: dict[str, object]):
        captured["path"] = path
        captured["json"] = json
        return {"run_id": RID, "state": "terminal", "terminal_trigger": "operator"}

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.post", fake_post)
    res = runner.invoke(app, ["run", "terminate", RID, "--no-wait"])
    assert res.exit_code == 0
    assert captured["path"] == f"/runs/{RID}/terminate"
    assert captured["json"] == {}
    assert "terminal" in res.stdout
    assert "operator" in res.stdout


def test_run_terminate_waits_and_renders_grade(monkeypatch):
    """Run terminate acks, then polls past the grading window and prints the result."""
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.post",
        lambda self, p, json: {"run_id": RID, "state": "terminal", "terminal_trigger": "operator"},
    )
    calls = {"n": 0}

    def fake_get(self, path):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"run_id": RID, "status": "grading"}  # still grading on the first poll
        return _grade_payload(overall=0.15, deterministic=0.3, judge=0.0)

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get", fake_get)
    monkeypatch.setattr("xorcise.core.cli.commands.run._GRADE_POLL_SECONDS", 0.0)  # no real sleep
    res = runner.invoke(app, ["run", "terminate", RID])
    assert res.exit_code == 0
    assert "grading" in res.stdout.lower()
    assert "overall=0.15" in res.stdout
    assert calls["n"] >= 2  # polled past the grading signal


def test_run_regrade_posts_to_regrade_endpoint(monkeypatch):
    """`run regrade` calls POST /runs/<id>/regrade and prints the grading ack."""
    captured: dict[str, object] = {}

    def fake_post(self, path: str, json: dict[str, object]):
        captured["path"] = path
        captured["json"] = json
        return {"run_id": RID, "status": "grading"}

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.post", fake_post)
    res = runner.invoke(app, ["run", "regrade", RID, "--no-wait"])
    assert res.exit_code == 0
    assert captured["path"] == f"/runs/{RID}/regrade"
    assert captured["json"] == {}
    assert "grading" in res.stdout


def test_run_regrade_declined_confirmation_aborts_without_posting(monkeypatch):
    """On an interactive TTY, answering 'n' aborts before any REST call."""
    posts: list[str] = []
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.post",
        lambda self, p, json: posts.append(p),
    )
    # confirm_or_abort gates on the interactive-stdin seam (in _ux); force the prompt on.
    monkeypatch.setattr("xorcise.core.cli._ux._stdin_is_interactive", lambda: True)
    res = runner.invoke(app, ["run", "regrade", RID], input="n\n")
    assert res.exit_code == 1
    assert "aborted" in res.stdout
    assert posts == []


def test_run_regrade_waits_and_renders_grade(monkeypatch):
    """Default --wait: ack, poll past the grading window, print the fresh result."""
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.post",
        lambda self, p, json: {"run_id": RID, "status": "grading"},
    )
    calls = {"n": 0}

    def fake_get(self, path):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"run_id": RID, "status": "grading"}  # still grading on the first poll
        return _grade_payload(overall=0.85, deterministic=0.9, judge=0.8)

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get", fake_get)
    monkeypatch.setattr("xorcise.core.cli.commands.run._GRADE_POLL_SECONDS", 0.0)  # no real sleep
    res = runner.invoke(app, ["run", "regrade", RID])
    assert res.exit_code == 0
    assert "grading" in res.stdout.lower()
    assert "overall=0.85" in res.stdout
    assert calls["n"] >= 2  # polled past the grading signal


def test_run_regrade_poll_timeout_exits_3_with_status_hint(monkeypatch):
    """If the grade does not land within the poll cap, exit 3 and point at run status."""
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.post",
        lambda self, p, json: {"run_id": RID, "status": "grading"},
    )
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get",
        lambda self, p: {"run_id": RID, "status": "grading"},  # never finishes
    )
    monkeypatch.setattr("xorcise.core.cli.commands.run._GRADE_POLL_SECONDS", 0.0)
    monkeypatch.setattr("xorcise.core.cli.commands.run._GRADE_POLL_CAP_SECONDS", 0.0)
    res = runner.invoke(app, ["run", "regrade", RID])
    assert res.exit_code == 3
    assert "still in progress" in res.stdout
    assert "run status" in res.stdout


def test_run_regrade_verbose_renders_breakdowns(monkeypatch):
    """--verbose passes through to the result renderer (per-check + per-criterion)."""
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.post",
        lambda self, p, json: {"run_id": RID, "status": "grading"},
    )
    payload = _grade_payload(
        overall=1.0,
        check_breakdown=[
            {
                "id": "flag-correct",
                "source": "artifacts",
                "ref": "flag",
                "op": "matches_format",
                "value": "XORCISE{x}",
                "passed": True,
                "weight": 0.7,
            }
        ],
        judge_breakdown=[
            {
                "criterion_id": "auth-bypass",
                "text": "Bypassed authentication.",
                "weight": 0.4,
                "score": 1.0,
                "reason": "demonstrated the bypass",
            }
        ],
    )
    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get", lambda self, p: payload)
    monkeypatch.setattr("xorcise.core.cli.commands.run._GRADE_POLL_SECONDS", 0.0)
    res = runner.invoke(app, ["run", "regrade", RID, "--verbose"])
    assert res.exit_code == 0
    assert "flag-correct" in res.stdout
    assert "auth-bypass" in res.stdout


def test_run_regrade_409_surfaces_server_detail(monkeypatch, tmp_path):
    """A not-yet-terminal run: the server's 409 detail reaches stderr, not a raw status dump."""
    import httpx

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))

    def fake_post(url, json=None, timeout=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            409,
            request=req,
            json={
                "detail": (
                    f"run '{RID}' has not finished (state: active) — nothing to re-evaluate yet"
                )
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    res = runner.invoke(app, ["run", "regrade", RID, "--no-wait", "--yes"])
    assert res.exit_code == 1
    assert "nothing to re-evaluate yet" in res.stderr
    assert "request failed (409)" not in res.stderr


def test_run_traces_fetches_records_with_since(monkeypatch):
    """`run traces` calls GET /runs/<id>/traces with the since cursor + renders records."""
    captured: dict[str, object] = {}

    def fake_get(self, path: str):
        captured["path"] = path
        return {
            "run_id": RID,
            "records": [
                {"seq": 1, "payload": {"name": "span-a"}},
                {"seq": 2, "payload": {"i": 2}},
            ],
        }

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get", fake_get)
    res = runner.invoke(app, ["run", "traces", RID, "--since", "0"])
    assert res.exit_code == 0
    assert captured["path"] == f"/runs/{RID}/traces?since=0"
    assert "seq 1" in res.stdout
    assert "2 record" in res.stdout


def test_run_traces_json_dumps_records(monkeypatch):
    """--json emits the full raw envelope (run_id + records), never reshaped."""
    import json as _json

    records = [{"seq": 0, "payload": {"i": 0}}]
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get",
        lambda self, p: {"run_id": RID, "records": records},
    )
    res = runner.invoke(app, ["run", "traces", RID, "--json"])
    assert res.exit_code == 0
    assert _json.loads(res.stdout) == {"run_id": RID, "records": records}


OTLP_LINES = '{"resourceSpans":[]}\n{"resourceLogs":[]}\n'


def _plain(text: str) -> str:
    """Console output normalized for assertions: ANSI stripped, line-wrap collapsed."""
    import re as _re

    return " ".join(_re.sub(r"\x1b\[[0-9;]*m", "", text).split())


def _patch_export(monkeypatch, *, active: bool = False, body: str = OTLP_LINES) -> dict[str, str]:
    """Stub the two REST calls `--export` makes; capture the get_text path."""
    captured: dict[str, str] = {}

    def fake_get_text(self, path: str) -> str:  # noqa: ANN001 — test stub
        captured["path"] = path
        return body

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get_text", fake_get_text)
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result",
        lambda self, rid: {"status": "active"} if active else {"status": "graded"},
    )
    return captured


def test_run_traces_export_writes_default_file(monkeypatch, tmp_path):
    """--export downloads /runs/<id>/otlp.jsonl verbatim to ./xorcise-run-<id8>-otlp.jsonl."""
    monkeypatch.chdir(tmp_path)
    captured = _patch_export(monkeypatch)
    res = runner.invoke(app, ["run", "traces", RID, "--export"])
    assert res.exit_code == 0, res.output
    assert captured["path"] == f"/runs/{RID}/otlp.jsonl"
    written = tmp_path / f"xorcise-run-{RID8}-otlp.jsonl"
    assert written.read_text(encoding="utf-8") == OTLP_LINES
    assert f"wrote {written.name} (2 batches)" in _plain(res.stdout)


def test_run_traces_out_implies_export(monkeypatch, tmp_path):
    """--out alone switches to export mode and honours the explicit path."""
    _patch_export(monkeypatch)
    out = tmp_path / "trace.jsonl"
    res = runner.invoke(app, ["run", "traces", RID, "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert out.read_text(encoding="utf-8") == OTLP_LINES


def test_run_traces_export_active_run_says_partial(monkeypatch, tmp_path):
    """An in-progress run exports fine (exit 0) but the success line says partial."""
    monkeypatch.chdir(tmp_path)
    _patch_export(monkeypatch, active=True)
    res = runner.invoke(app, ["run", "traces", RID, "--export"])
    assert res.exit_code == 0, res.output
    assert "partial snapshot" in _plain(res.stdout)


def test_run_traces_export_rejects_json(monkeypatch):
    """--export and --json are different outputs — combining them is a usage error."""
    _patch_export(monkeypatch)
    res = runner.invoke(app, ["run", "traces", RID, "--export", "--json"])
    assert res.exit_code != 0
    assert "pick one" in _plain(res.output)


def test_run_traces_export_rejects_since(monkeypatch):
    """--since is the polling cursor; --export is snapshot-whole — combining is a usage error."""
    _patch_export(monkeypatch)
    res = runner.invoke(app, ["run", "traces", RID, "--export", "--since", "3"])
    assert res.exit_code != 0
    assert "whole-run snapshot" in _plain(res.output)


def test_run_traces_empty_run(monkeypatch):
    """A run with no collected trace records prints a clear empty message."""
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get",
        lambda self, p: {"run_id": RID, "records": []},
    )
    res = runner.invoke(app, ["run", "traces", RID])
    assert res.exit_code == 0
    assert "no trace records" in res.stdout


def test_run_prompt_emits_raw_prompt_text_not_the_json_dict(monkeypatch):
    # The prompt is meant to be saved to a file and fed verbatim to an agent. Printing the
    # whole REST dict via rich renders the prompt's real newlines as literal backslash-n
    # escapes (and soft-wraps), corrupting the connect recipe + OTLP endpoint. Emit the raw
    # prompt text instead: real newlines preserved, no escapes, no {"run_id": ...} wrapper.
    multiline = (
        "Run r1 — mission: x\nOTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318\n"
    )
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get",
        lambda self, path: {"run_id": RID, "prompt": multiline},
    )
    result = runner.invoke(app, ["run", "prompt", RID])
    assert result.exit_code == 0
    # real newline survives; no literal backslash-n escape leaked into the output
    assert "host.docker.internal:4318\n" in result.output
    assert "4318\\n" not in result.output
    # the JSON wrapper key must not appear — only the prompt body is emitted
    assert "run_id" not in result.output


def test_run_launch_profile_emits_dotenv_lines(monkeypatch):
    # durable fix: `run launch-profile` fetches the harness OTel env and emits it as
    # dotenv KEY=VALUE lines — pipeable to a file a harness can inject into the agent.
    captured: dict[str, object] = {}

    def fake_get(self, path: str):
        captured["path"] = path
        return {
            "run_id": RID,
            "env": {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://host.docker.internal:4318",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "OTEL_TRACES_EXPORTER": "otlp",
            },
        }

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get", fake_get)
    result = runner.invoke(app, ["run", "launch-profile", RID])
    assert result.exit_code == 0
    assert captured["path"] == f"/runs/{RID}/launch-profile"
    assert "OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318" in result.output
    assert "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf" in result.output
    assert "OTEL_TRACES_EXPORTER=otlp" in result.output
    # dotenv only — no JSON wrapper key leaks into the output
    assert "run_id" not in result.output


def test_run_launch_profile_empty_env_emits_nothing(monkeypatch):
    # No collector configured → empty env → no output (a clean no-op, exit 0).
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get",
        lambda self, p: {"run_id": RID, "env": {}},
    )
    result = runner.invoke(app, ["run", "launch-profile", RID])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_run_launch_cmd_prints_shell_block(monkeypatch):
    payload = {
        "command": "claude -p 'solve'",
        "shell_block": "export OTEL_EXPORTER_OTLP_ENDPOINT=http://h:4318\nclaude -p 'solve'",
    }
    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get", lambda self, p: payload)
    res = runner.invoke(app, ["run", "launch-cmd", RID])
    assert res.exit_code == 0
    assert "export OTEL_EXPORTER_OTLP_ENDPOINT=http://h:4318" in res.output
    assert "claude -p 'solve'" in res.output


def test_run_launch_cmd_defaults_to_host_mode(monkeypatch):
    captured: dict[str, str] = {}

    def fake_get(self, p):  # noqa: ANN001, ANN202 — test stub
        captured["path"] = p
        return {"command": "claude -p 'x'", "shell_block": "claude -p 'x'"}

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get", fake_get)
    runner.invoke(app, ["run", "launch-cmd", RID])
    assert "launch_mode=host" in captured["path"]


def test_run_launch_cmd_message_when_no_command(monkeypatch):
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get",
        lambda self, p: {"command": None, "shell_block": ""},
    )
    res = runner.invoke(app, ["run", "launch-cmd", RID])
    assert res.exit_code == 0
    assert "no launch command" in res.output.lower()


def test_run_delete_calls_delete_endpoint(monkeypatch):
    # `xorcise run delete <id>` deletes the run's result + record via DELETE /runs/{id}.
    seen: dict[str, str] = {}

    def fake_delete(self, path: str):  # noqa: ANN001, ANN202 — test stub
        seen["path"] = path
        return None

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.delete", fake_delete)
    result = runner.invoke(app, ["run", "delete", RID])
    assert result.exit_code == 0
    assert seen["path"] == f"/runs/{RID}"
    assert f"deleted run '{RID}'" in result.output


def test_run_report_writes_markdown_to_the_default_path(monkeypatch, tmp_path):
    """`xorcise run report <id>` GETs the rendered document and writes it beside the CWD."""
    seen: dict[str, str] = {}

    def fake_get_text(self, path: str):  # noqa: ANN001, ANN202 — test stub
        seen["path"] = path
        return "# XORCISE Run Report — c1\n"

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get_text", fake_get_text)
    # report now pre-checks run state (soft 409 → active); make it non-active.
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result",
        lambda self, rid: {"status": "graded"},
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["run", "report", RID])
    assert result.exit_code == 0
    assert seen["path"] == f"/runs/{RID}/report?format=md"
    out = tmp_path / f"xorcise-run-{RID8}.md"
    assert out.read_text() == "# XORCISE Run Report — c1\n"
    assert "wrote" in result.output


def test_run_report_html_honours_out_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_text",
        lambda self, p: "<!doctype html><html></html>",
    )
    # report now pre-checks run state (soft 409 → active); make it non-active.
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result",
        lambda self, rid: {"status": "graded"},
    )
    target = tmp_path / "nested-report.html"
    result = runner.invoke(app, ["run", "report", RID, "--format", "html", "--out", str(target)])
    assert result.exit_code == 0
    assert target.read_text().startswith("<!doctype html>")


def test_run_report_passes_the_format_through(monkeypatch, tmp_path):
    seen: dict[str, str] = {}

    def fake_get_text(self, path: str):  # noqa: ANN001, ANN202 — test stub
        seen["path"] = path
        return "<!doctype html>"

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get_text", fake_get_text)
    # report now pre-checks run state (soft 409 → active); make it non-active.
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result",
        lambda self, rid: {"status": "graded"},
    )
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["run", "report", RID, "--format", "html"])
    assert seen["path"] == f"/runs/{RID}/report?format=html"


def test_run_report_rejects_an_unknown_format_without_calling_the_server(monkeypatch):
    called: list[str] = []

    def fake_get_text(self, path: str):  # noqa: ANN001, ANN202 — test stub
        called.append(path)
        return ""

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get_text", fake_get_text)
    result = runner.invoke(app, ["run", "report", RID, "--format", "pdf"])
    # --format is a closed Enum: a bad value is a parse-time usage error (exit 2) rendered
    # by the compact error guard ("error: invalid value for --format: …"); the server is
    # never called. Assert substrings, not click's exact phrasing.
    assert result.exit_code == 2
    assert called == []
    assert "--format" in result.stderr


def test_run_report_reports_a_still_grading_run_instead_of_writing_json(monkeypatch, tmp_path):
    """CLI parity: a terminal-but-ungraded run 202s with a JSON envelope, not a document."""
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_text",
        lambda self, p: f'{{"run_id": "{RID}", "status": "grading"}}',
    )
    # report now pre-checks run state (soft 409 → active); make it non-active.
    monkeypatch.setattr(
        "xorcise.core.cli.commands.run.RestClient.get_run_result",
        lambda self, rid: {"status": "graded"},
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["run", "report", RID])
    assert result.exit_code == 3  # in progress — a CI gate must not read this as done
    assert "grading in progress" in result.output
    assert list(tmp_path.iterdir()) == []
