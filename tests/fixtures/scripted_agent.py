"""Scripted reference agent — the e2e fixture + executable spec.

Lives in tests/ ONLY (never shipped; XORCISE is vendor-neutral about the agent
under test). Drives the fully stubbed loop over the PRIMARY REST/FastAPI surface
and emits OTel. The two hard requirements on any agent are the headline methods:
emit_spans (emits OTel tagged with run_id) and join_network (joins the per-run
network — stubbed here; a real served-Tailscale client is out of the fixture's
scope).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class ScriptedAgent:
    rest_url: str  # e.g. http://127.0.0.1:3001/api
    otlp_url: str  # e.g. http://127.0.0.1:4318
    name: str = "scripted_reference_agent"
    joined: bool = False
    spans_emitted: int = 0
    _run_control_key: str = ""  # threaded from create_run response

    # --- REST: register + run lifecycle -------------------------------------
    def register(self) -> dict[str, Any]:
        resp: dict[str, Any] = httpx.post(
            f"{self.rest_url}/agents", json={"name": self.name}, timeout=5
        ).json()
        return resp

    def create_run(self, agent_name: str, mission: str = "ref-001") -> str:
        # A run is created for a registered agent, selected by its (unique) name —
        # the whole agent surface is name-keyed (register/rm/history); id is internal.
        # run_control_key is returned in the create response and stored for /complete.
        body = httpx.post(
            f"{self.rest_url}/runs",
            json={"agent": agent_name, "mission": mission},
            timeout=5,
        ).json()
        run_id: str = body["run_id"]
        self._run_control_key = body.get("run_control_key", "")
        return run_id

    def consume_connect_prompt(self, run_id: str) -> str:
        body = httpx.get(f"{self.rest_url}/runs/{run_id}/prompt", timeout=5).json()
        prompt: str = body["prompt"]
        return prompt

    def join_network(self, prompt: str) -> bool:
        """Hard requirement #2. Stub: consuming the connect prompt
        'joins' the per-run tailnet; a real served-Tailscale client is out of scope."""
        self.joined = bool(prompt)
        return self.joined

    def start_mission(self, run_id: str) -> dict[str, Any]:
        resp: dict[str, Any] = httpx.post(f"{self.rest_url}/runs/{run_id}/start", timeout=5).json()
        return resp

    # --- OTel: emit spans (hard requirement #1) -----------------------------
    @staticmethod
    def _otlp_payload(run_id: str, names: list[str]) -> dict[str, Any]:
        """Minimal OTLP/HTTP JSON envelope carrying run_id as a resource attribute
        (full span mapping lives in core.otel; here it proves the seam + run-tagging)."""
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [{"key": "xorcise.run_id", "value": {"stringValue": run_id}}]
                    },
                    "scopeSpans": [{"spans": [{"name": n} for n in names]}],
                }
            ]
        }

    def emit_spans(self, run_id: str, names: list[str] | None = None) -> int:
        names = names or ["connect", "act", "submit"]
        httpx.post(
            f"{self.otlp_url}/v1/traces",
            json=self._otlp_payload(run_id, names),
            timeout=5,
        )
        self.spans_emitted = len(names)
        return self.spans_emitted

    # --- REST: submission ---------------------------------------------------
    def submit(self, run_id: str, flag: str = "XORCISE{stub}") -> dict[str, Any]:
        _auth = {"Authorization": f"Bearer {self._run_control_key}"}
        httpx.post(
            f"{self.rest_url}/runs/{run_id}/artifacts",
            json={"name": "solution.txt"},
            headers=_auth,
            timeout=5,
        )
        # the flag is the manifest artifact named "flag", submitted via the one
        # extensible submission endpoint (the single-purpose /flag endpoint was removed).
        flag_res = httpx.post(
            f"{self.rest_url}/runs/{run_id}/artifacts",
            json={"name": "flag", "content": flag},
            headers=_auth,
            timeout=5,
        ).json()
        done_res = httpx.post(
            f"{self.rest_url}/runs/{run_id}/complete",
            headers=_auth,
            timeout=5,
        ).json()
        return {"flag": flag_res, "done": done_res}

    def fetch_result(self, run_id: str, *, timeout_s: float = 30.0) -> dict[str, Any]:
        """Poll past the async grading window: against a real server the run is sealed
        on /complete and graded on a background task, so /result returns 202 {"status":"grading"}
        until the grade lands. (Under the in-process TestClient the task runs inline and the first
        GET already returns 200.)"""
        deadline = time.monotonic() + timeout_s
        while True:
            resp = httpx.get(f"{self.rest_url}/runs/{run_id}/result", timeout=5)
            if resp.status_code == 202 and time.monotonic() < deadline:
                time.sleep(0.5)
                continue
            body: dict[str, Any] = resp.json()
            return body

    # --- orchestration ------------------------------------------------------
    def run(self, mission: str = "ref-001") -> dict[str, Any]:
        """Drive the full stubbed loop over REST + OTLP; return a summary."""
        agent = self.register()
        run_id = self.create_run(agent["name"], mission)
        prompt = self.consume_connect_prompt(run_id)
        self.join_network(prompt)
        self.start_mission(run_id)
        self.emit_spans(run_id)
        submit = self.submit(run_id)
        result = self.fetch_result(run_id)
        return {
            "run_id": run_id,
            "joined": self.joined,
            "spans_emitted": self.spans_emitted,
            "submit": submit,
            "result": result,
        }
