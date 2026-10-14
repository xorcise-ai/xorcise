"""Integration: a real `serve` process stopped by a real signal folds the WAL into xorcise.db.

`down` stops the server with SIGTERM, so the shutdown checkpoint has to run on that path, not
only on Ctrl-C. Faking `serve_bound` cannot see this: uvicorn re-raises the signal it caught
into whatever handler was installed before it, after the servers stop.
"""

from __future__ import annotations

import os
import pathlib
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT], ids=["sigterm", "sigint"])
def test_a_signalled_serve_leaves_no_wal_behind(tmp_path: pathlib.Path, sig: int) -> None:
    rest, otlp = _free_port(), _free_port()
    env = {**os.environ, "XORCISE_HOME": str(tmp_path)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "xorcise", "serve", "--stub"]
        + ["--port", str(rest), "--otlp-port", str(otlp)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                pytest.fail(f"server exited early (code {proc.returncode}):\n{out}")
            try:
                if httpx.get(f"http://127.0.0.1:{rest}/api/health", timeout=1).is_success:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("server did not become healthy")
            time.sleep(0.25)
        # A write the server holds in the WAL until the checkpoint folds it in.
        httpx.post(f"http://127.0.0.1:{rest}/api/agents", json={"name": "wal"}, timeout=5)
        wal = tmp_path / "xorcise.db-wal"
        assert wal.exists() and wal.stat().st_size > 0  # else the assertion below proves nothing

        proc.send_signal(sig)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert proc.returncode == 0
    assert not wal.exists() or wal.stat().st_size == 0
