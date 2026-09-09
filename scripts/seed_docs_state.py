#!/usr/bin/env python3
"""Seed a XORCISE instance with the state the documentation screenshots need.

Screenshots of an empty console are worse than no screenshots: they show empty states, and a
reader comparing their own screen against one learns nothing. This script drives a running
instance through the REST API and the OTLP receiver until every page the docs photograph has
something real on it — registered agents, installed missions, and a run carrying genuine
captured telemetry.

It is deliberately NOT a fixture loader that writes to the database. Everything here goes
through the same public surfaces an operator uses, so a change that breaks seeding is a change
that broke the product, not the fixture.

SAFETY: this creates agents, installs missions and starts runs. Point it ONLY at a throwaway
instance — one booted with its own XORCISE_HOME and its own port. It refuses to run against
the default port unless --i-know-this-is-not-my-real-instance is passed.

Usage:
    # boot a throwaway instance first
    export XORCISE_HOME=/tmp/xorcise-docs XORCISE_REST_PORT=3051 XORCISE_OTLP_PORT=4351
    xorcise up --stub

    python scripts/seed_docs_state.py --rest http://127.0.0.1:3051 --otlp http://127.0.0.1:4351
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "otlp" / "claude_code_real_run.json"

#: Agents to register. The three built-in harnesses, so the Agents page shows the real
#: per-harness logos rather than three identical generic cards.
AGENTS: tuple[tuple[str, str], ...] = (
    ("scout", "claude-code"),
    ("atlas", "openhands"),
    ("probe", "codex"),
)

#: Missions to install. Two, so the catalog shows both installed and available states side by
#: side — a catalog where everything is installed photographs as badly as an empty one.
MISSIONS: tuple[str, ...] = ("chrono-canary", "breachpoint")

#: The mission the screenshotted run is against. Its terrain is drawn on the live run page, so
#: it wants to be a mission with an authored attack path rather than a bare one.
RUN_MISSION = "chrono-canary"
RUN_AGENT = "scout"


def _request(method: str, url: str, body: object | None = None, timeout: float = 60.0) -> object:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise SystemExit(f"{method} {url} -> {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"{method} {url} unreachable: {exc.reason}\nIs the instance up?") from exc


def seed_agents(rest: str) -> None:
    existing = {a["name"] for a in _request("GET", f"{rest}/api/agents")}  # type: ignore[union-attr]
    for name, kind in AGENTS:
        if name in existing:
            print(f"  agent {name} already registered")
            continue
        _request("POST", f"{rest}/api/agents", {"name": name, "kind": kind})
        print(f"  registered {name} ({kind})")


def seed_missions(rest: str) -> None:
    catalog = _request("GET", f"{rest}/api/missions")
    by_id = {m["mission_id"]: m for m in catalog}  # type: ignore[union-attr]
    for mission_id in MISSIONS:
        entry = by_id.get(mission_id)
        if entry is None:
            print(f"  ! {mission_id} is not in this catalog — skipping")
            continue
        if entry.get("installed"):
            print(f"  mission {mission_id} already installed")
            continue
        # Synchronous pull. Under --stub the driver is the stub, so no bytes move.
        _request("POST", f"{rest}/api/missions/{mission_id}/pull", {}, timeout=900.0)
        print(f"  installed {mission_id}")


def create_run(rest: str) -> str:
    run = _request(
        "POST",
        f"{rest}/api/runs",
        {"agent": RUN_AGENT, "mission": RUN_MISSION, "budget_seconds": 1800},
    )
    run_id = run["run_id"]  # type: ignore[index]
    print(f"  created run {run_id[:8]} ({RUN_AGENT} vs {RUN_MISSION})")
    return str(run_id)


def replay_telemetry(otlp: str, run_id: str) -> int:
    """Replay a captured claude-code trace into the run, re-stamped with this run's id.

    The payloads are real OTLP captured from a real agent, so the trace feed and the timeline
    show an actual agent's prose and tool calls rather than lorem ipsum. Only the
    `xorcise.run_id` resource attribute is rewritten — that attribute is how the collector
    routes a batch to a run, and nothing else about the capture is touched.
    """
    if not FIXTURE.exists():
        raise SystemExit(f"missing OTLP fixture: {FIXTURE}")
    payloads = json.loads(FIXTURE.read_text())["records"]
    for record in payloads:
        payload = record["payload"]
        for resource_span in payload.get("resourceSpans", []):
            attrs = [
                a
                for a in resource_span.setdefault("resource", {}).setdefault("attributes", [])
                if a.get("key") != "xorcise.run_id"
            ]
            attrs.append({"key": "xorcise.run_id", "value": {"stringValue": run_id}})
            resource_span["resource"]["attributes"] = attrs
        _request("POST", f"{otlp}/v1/traces", payload, timeout=30.0)
    print(f"  replayed {len(payloads)} OTLP batch(es)")
    return len(payloads)


def seal(rest: str, run_id: str) -> None:
    """Terminate and wait for grading, so the result page has something to render."""
    _request("POST", f"{rest}/api/runs/{run_id}/terminate", {}, timeout=120.0)
    for _ in range(30):
        result = _request("GET", f"{rest}/api/runs/{run_id}/result")
        if not isinstance(result, dict) or result.get("status") != "grading":
            print("  run sealed and graded")
            return
        time.sleep(2)
    print("  ! still grading after 60s — screenshots of the result page may show the spinner")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rest", default="http://127.0.0.1:3051")
    ap.add_argument("--otlp", default="http://127.0.0.1:4351")
    ap.add_argument("--keep-active", action="store_true",
                    help="Do not terminate the run, so the live run page shows an ACTIVE run.")
    ap.add_argument("--i-know-this-is-not-my-real-instance", action="store_true")
    args = ap.parse_args()

    if ":3001" in args.rest and not args.i_know_this_is_not_my_real_instance:
        raise SystemExit(
            "refusing to seed http://…:3001 — that is the default port, and this script "
            "registers agents and starts runs. Boot a throwaway instance on another port "
            "(XORCISE_HOME + XORCISE_REST_PORT), or pass "
            "--i-know-this-is-not-my-real-instance."
        )

    health = _request("GET", f"{args.rest}/api/health", timeout=10.0)
    print(f"instance: {args.rest} ({health})")

    print("agents:")
    seed_agents(args.rest)
    print("missions:")
    seed_missions(args.rest)
    print("run:")
    run_id = create_run(args.rest)
    replay_telemetry(args.otlp, run_id)
    time.sleep(2)  # let the collector commit the batches before anything reads them
    if not args.keep_active:
        seal(args.rest, run_id)

    # The spec needs the run id; stdout's last line is the contract.
    print(f"RUN_ID={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
