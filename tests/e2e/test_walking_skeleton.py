import pathlib
import subprocess
import sys
import time

import httpx
import pytest

REST = "http://127.0.0.1:3001"


@pytest.mark.e2e
def test_up_serves_ui_then_down(tmp_path: pathlib.Path) -> None:
    env = {"XORCISE_HOME": str(tmp_path), "PATH": __import__("os").environ["PATH"]}
    # Migrations are explicit, never on boot: migrate before serving
    # the real (DB-backed) agent endpoints.
    subprocess.run(
        [sys.executable, "-m", "xorcise", "db", "upgrade"],
        env=env,
        check=True,
        capture_output=True,
    )
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
        assert "XORCISE" in httpx.get(f"{REST}/ui/", timeout=2).text
        # The registry is real + empty by default (no canned seed). Register one
        # and confirm it round-trips through the live API.
        registered = httpx.post(
            f"{REST}/api/agents", json={"name": "walking-skeleton"}, timeout=2
        ).json()
        assert registered["id"]
        listed = httpx.get(f"{REST}/api/agents", timeout=2).json()
        assert any(a["name"] == "walking-skeleton" for a in listed)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
