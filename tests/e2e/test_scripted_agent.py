"""e2e: xorcise serve + the scripted reference agent complete the stubbed loop.

Same script must later pass unchanged against the real loop (rails didn't move).
"""

import os
import pathlib
import subprocess
import sys
import time

import httpx
import pytest

from tests._helpers import install_mission
from tests.fixtures.scripted_agent import ScriptedAgent

REST = "http://127.0.0.1:3001"
OTLP = "http://127.0.0.1:4318"


@pytest.mark.e2e
def test_scripted_agent_completes_loop(tmp_path: pathlib.Path) -> None:
    # This is the Docker-free stub loop (record-only flag submission + canned result), so the
    # spawned server must use the stub adapters. Real-by-default otherwise tries real
    # Docker on `run create` and 500s without a daemon — opt into stubs explicitly here.
    env = {
        "XORCISE_HOME": str(tmp_path),
        "PATH": os.environ["PATH"],
        "XORCISE_USE_STUBS": "1",
    }
    # Migrations are an explicit step, never on boot. The operator
    # runs `xorcise db upgrade`; do the same before the server serves the real
    # (DB-backed) agent/run/result endpoints.
    subprocess.run(
        [sys.executable, "-m", "xorcise", "db", "upgrade"],
        env=env,
        check=True,
        capture_output=True,
    )
    # Creating a run is gated on an installed mission: the operator pulls
    # the mission before the agent can run it. Provision the reference mission on
    # disk here — the agent script itself stays unchanged (rails didn't move).
    install_mission(tmp_path, "ref-001")
    proc = subprocess.Popen(
        [sys.executable, "-m", "xorcise", "serve"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    try:
        ok = False
        for _ in range(60):
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                pytest.fail(f"server exited early (code {proc.returncode}):\n{out}")
            try:
                if httpx.get(f"{REST}/api/health", timeout=1).status_code == 200:
                    ok = True
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        if not ok:
            proc.terminate()
            proc.wait(timeout=10)
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            pytest.fail(f"server did not become healthy:\n{out}")

        agent = ScriptedAgent(rest_url=f"{REST}/api", otlp_url=OTLP)
        summary = agent.run()

        assert summary["joined"] is True
        assert summary["spans_emitted"] >= 1
        # The flag is the manifest artifact named "flag", submitted via /artifacts
        # (the single-purpose /flag endpoint was removed). The submission is record-only;
        # correctness is deferred to grading. /complete (below) drives terminal.
        assert summary["submit"]["flag"]["accepted"] is True
        assert summary["submit"]["flag"]["name"] == "flag"
        assert summary["submit"]["done"]["state"] == "terminal"
        # /result now returns the composite {grade, conditions}. The canned 0.5 is
        # gone: termination now runs the REAL grader (run_terminate), so the stub loop — no
        # correct flag, no judge model configured — grades 0.0 across both halves.
        assert summary["result"]["grade"]["overall"] == 0.0
        assert summary["result"]["grade"]["run_id"] == summary["run_id"]
        # disclosed conditions ride along; judge unconfigured in the stub loop → None
        assert summary["result"]["conditions"]["judge_model"] is None
    finally:
        proc.terminate()
        proc.wait(timeout=10)
