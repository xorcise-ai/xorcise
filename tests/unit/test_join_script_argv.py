"""Run the served join script and check that no secret reaches any process's argv (#172).

Any local user can read every process command line through `ps` or /proc/<pid>/cmdline. The
template tests in test_join_script.py pin the shapes that keep the join key, the run-control bearer
and the watch token off them; this runs the real script, piped to `sh` the way an agent runs it,
and records the argv of everything it starts.

The script runs with a PATH of Python stubs (the Tailscale client, curl, sleep, docker) in front of
an argv-logging wrapper for EVERY executable on this host's PATH — each logs, then execs the real
tool. So any command the script runs, today or after a later edit, is recorded; a hand-kept list of
"the commands the script uses" would miss exactly the new one that matters, all the more so behind
the script's `2>/dev/null || true` guards. Shell builtins (printf, echo, kill, command, [) never
exec, so they never have an argv to leak. The native path is exercised end to end — join, the
detached self-reaper, its poll, and its teardown; the sidecar needs a Docker daemon and is covered
by the template tests.
"""

from __future__ import annotations

import contextlib
import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest

from xorcise.core.runs.join import TAILSCALE_CLIENT_VERSION, render_join_script

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        sys.platform != "linux" or shutil.which("setsid") is None or shutil.which("pgrep") is None,
        reason="drives the Linux native join path (setsid, pgrep, /proc)",
    ),
]

JOIN_KEY = "hskey-auth-JOINSECRET172"
BEARER = "rk-BEARERSECRET172"
WATCH = "wk-WATCHSECRET172"
_CA_PEM = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"

_STUBBED = frozenset({"tailscale", "tailscaled", "curl", "sleep", "docker"})
_SAFE_NAME = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-")

_LOG_PREAMBLE = f"""#!{sys.executable}
import os, sys
with open(os.environ["XORCISE_ARGV_LOG"], "a") as log:
    log.write(" ".join(sys.argv) + "\\n")
stub = os.environ["XORCISE_STUB_DIR"]
"""

_TAILSCALED = (
    _LOG_PREAMBLE
    + """
import socket, time
sock = next(a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--socket="))
# Bind relative to the socket's directory: a pytest tmp path can exceed AF_UNIX's 108 bytes.
os.chdir(os.path.dirname(sock))
s = socket.socket(socket.AF_UNIX)
s.bind(os.path.basename(sock))
s.listen()
time.sleep(120)
"""
)

# Three generations of client, told apart the way the script tells them apart — by `up --help`:
#   current   `--auth-key`, reads a key file
#   renamed   reads a key file but predates the `--auth-key` spelling, so knows only `--authkey`
#   nofile    `--authkey` only, and no key file — the key could only go on its argv
_HELP = {
    "current": '  --auth-key value\n    \tnode authorization key; if it begins with "file:", …',
    "renamed": '  -authkey string\n    \tnode authorization key; if it begins with "file:", …',
    "nofile": "  -authkey string\n    \tnode authorization key",
}
_TAILSCALE = (
    _LOG_PREAMBLE
    + """
import time
args = sys.argv[1:]
flag = "--auth-key" if KIND == "current" else "--authkey"
if "up" in args and "--help" in args:
    print(HELP)
    sys.exit(0)
if "up" in args:
    given = [a for a in args if a.startswith(("--auth-key", "--authkey"))]
    if not given or not given[0].startswith(flag + "=file:"):
        print("flag provided but not defined", file=sys.stderr)
        sys.exit(2)
    path = given[0].split("=file:", 1)[1]
    def put(name, value):
        with open(os.path.join(stub, name), "w") as out:
            out.write(value)
    put("seen_key", open(path).read())
    put("seen_mode", oct(os.stat(path).st_mode & 0o777))
    put("seen_work_mode", oct(os.stat(os.path.dirname(path)).st_mode & 0o777))
    ca = os.path.join(os.path.dirname(path), "ca.pem")
    put("seen_ca_mode", oct(os.stat(ca).st_mode & 0o777))
    put("seen_by", sys.argv[0])
    time.sleep(float(os.environ.get("XORCISE_STUB_UP_SECONDS", "0")))
elif "ip" in args:
    print("100.64.0.9")
elif "down" in args:
    open(os.path.join(stub, "down"), "w").close()
"""
)

_CURL = (
    _LOG_PREAMBLE
    + """
import shutil
args = sys.argv[1:]
if "-K" in args and args[args.index("-K") + 1] == "-":
    with open(os.path.join(stub, "curl_stdin"), "a") as out:
        out.write(sys.stdin.read())
if "-w" in args:
    print("409", end="")  # the run has ended: the reaper's cue to tear down
elif "-o" in args:
    tgz = os.path.join(stub, "client.tgz")
    if not os.path.exists(tgz):
        sys.exit(22)
    shutil.copy(tgz, args[args.index("-o") + 1])
"""
)

_SLEEP = _LOG_PREAMBLE + "import time\ntime.sleep(0.1)\n"
# No daemon: keeps the run hermetic and on the native path.
_DOCKER = _LOG_PREAMBLE + "sys.exit(1)\n"


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _tailscale_stub(kind: str) -> str:
    consts = f"KIND = {kind!r}\nHELP = {_HELP[kind]!r}\n"
    return _TAILSCALE.replace("stub = os.environ", consts + "stub = os.environ", 1)


def _arch() -> str:
    arches = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    return arches.get(platform.machine(), "amd64")


@pytest.fixture(scope="module")
def wrapped_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A logging wrapper for every executable on this host's PATH, first match winning as PATH
    lookup does. Built once per module: it is a few thousand small files."""
    out = tmp_path_factory.mktemp("wrapped")
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory or not os.path.isdir(directory):
            continue
        for entry in os.scandir(directory):
            name = entry.name
            if name in _STUBBED or not set(name) <= _SAFE_NAME or (out / name).exists():
                continue
            if not (entry.is_file() and os.access(entry.path, os.X_OK)):
                continue
            _write_exe(
                out / name,
                f'#!/bin/sh\nprintf \'%s \' "$0" "$@" >>"$XORCISE_ARGV_LOG"\n'
                f'printf \'\\n\' >>"$XORCISE_ARGV_LOG"\nexec {entry.path} "$@"\n',
            )
    return out


class _Harness:
    def __init__(
        self, root: Path, wrapped: Path, *, client: str = "current", serve_client: bool = False
    ):
        self.root = root
        self.bin = root / "bin"
        self.stub = root / "stub"
        self.parent = root / "work"
        for d in (self.bin, self.stub, self.parent):
            d.mkdir()
        self.parent.chmod(0o700)
        self.path = f"{self.bin}{os.pathsep}{wrapped}"
        self.log = root / "argv.log"
        self.log.touch()
        _write_exe(self.bin / "tailscale", _tailscale_stub(client))
        _write_exe(self.bin / "tailscaled", _TAILSCALED)
        _write_exe(self.bin / "curl", _CURL)
        _write_exe(self.bin / "sleep", _SLEEP)
        _write_exe(self.bin / "docker", _DOCKER)
        if serve_client:
            self._pack_client()

    def _pack_client(self) -> None:
        """The pinned client tarball, as run-control serves it, holding new-enough stubs."""
        top = f"tailscale_{TAILSCALE_CLIENT_VERSION}_{_arch()}"
        src = self.root / "pkg" / top
        src.mkdir(parents=True)
        _write_exe(src / "tailscale", _tailscale_stub("current"))
        _write_exe(src / "tailscaled", _TAILSCALED)
        with tarfile.open(self.stub / "client.tgz", "w:gz") as tgz:
            tgz.add(src, arcname=top)

    def script(self) -> str:
        return render_join_script(
            login_server="https://172.17.0.1:443",
            join_key=JOIN_KEY,
            ca_cert=_CA_PEM,
            run_control_base="http://127.0.0.1:9/api/runs/run-172",
            run_control_key=BEARER,
            watch_token=WATCH,
            reap_cap_seconds=60,
        )

    def env(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": self.path,
            "HOME": str(self.root),
            "XORCISE_TS_DIR": str(self.parent),
            "XORCISE_TAILSCALE_MODE": "native",
            "XORCISE_ARGV_LOG": str(self.log),
            "XORCISE_STUB_DIR": str(self.stub),
            **extra,
        }

    def run(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh"],
            input=self.script(),
            text=True,
            capture_output=True,
            timeout=60,
            env=self.env(**extra),
        )

    def wait_for_reaper(self) -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if (self.stub / "down").exists() and not self._live_pids():
                return
            time.sleep(0.1)
        pytest.fail("the self-reaper never tore the stub daemon down")

    def _live_pids(self) -> list[int]:
        pids = []
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                cmdline = (proc / "cmdline").read_bytes()
            except OSError:
                continue
            if str(self.root).encode() in cmdline and int(proc.name) != os.getpid():
                pids.append(int(proc.name))
        return pids

    def kill_leftovers(self) -> None:
        for pid in self._live_pids():
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)

    def argv_log(self) -> str:
        return self.log.read_text()


@pytest.fixture
def harness(tmp_path, wrapped_path):
    made: list[_Harness] = []

    def make(**kw: Any) -> _Harness:
        h = _Harness(tmp_path, wrapped_path, **kw)
        made.append(h)
        return h

    yield make
    for h in made:
        h.kill_leftovers()


def _assert_no_secret_in_any_argv(log: str) -> None:
    assert log.strip(), "nothing was recorded — the wrappers did not run"
    for secret in (JOIN_KEY, BEARER, WATCH):
        leaked = [line for line in log.splitlines() if secret in line]
        assert not leaked, f"{secret} reached an argv:\n" + "\n".join(leaked)


def test_a_native_join_and_its_reaper_put_no_secret_on_any_command_line(harness):
    h = harness()
    result = h.run()
    assert result.returncode == 0, result.stderr
    assert "joined tailnet as 100.64.0.9" in result.stdout
    h.wait_for_reaper()

    log = h.argv_log()
    _assert_no_secret_in_any_argv(log)
    # The checks ran over the processes that used to carry the secrets.
    assert " up " in log and "--auth-key=file:" in log  # tailscale up, current spelling
    assert "sh -c " in log  # the detached self-reaper
    assert "/alive" in log  # its poll
    # The key still arrived, through a private file that is gone afterwards.
    assert (h.stub / "seen_key").read_text() == JOIN_KEY
    assert (h.stub / "seen_mode").read_text() == oct(0o600)
    assert not list(h.parent.glob("*/authkey"))
    # The reaper removed its work dir: a parent outside /tmp no longer collects one per join.
    assert not list(h.parent.iterdir())
    # The reaper authenticated with the watch token, over stdin, and never with the bearer.
    polled = (h.stub / "curl_stdin").read_text()
    assert f'header = "Authorization: Bearer {WATCH}"' in polled
    assert BEARER not in polled


def test_the_work_dir_is_private_and_inside_the_requested_parent(harness):
    h = harness()
    result = h.run()
    assert result.returncode == 0, result.stderr
    work = [line for line in h.argv_log().splitlines() if "mktemp -d" in line]
    assert work and f"{h.parent}/xorcise-ts.XXXXXX" in work[0]
    # Read while `up` ran, before the reaper removed the dir.
    assert (h.stub / "seen_work_mode").read_text() == oct(0o700)
    assert (h.stub / "seen_ca_mode").read_text() == oct(0o644)  # public, mountable by the sidecar
    h.wait_for_reaper()


def test_a_parent_another_user_could_write_to_is_refused(harness):
    h = harness()
    h.parent.chmod(0o777)  # writable by others and not sticky: they could swap our dir out
    result = h.run()
    assert result.returncode == 1
    assert "refusing to keep the join key under" in result.stderr
    assert not list(h.parent.iterdir())  # nothing was created there
    assert not (h.stub / "seen_key").exists()


def test_a_parent_reached_through_a_symlink_is_judged_by_its_target(harness, tmp_path):
    # `ls -ld` on a link reports the link (lrwxrwxrwx), not the directory it points to.
    h = harness()
    link = tmp_path / "parent-link"
    link.symlink_to(h.parent)  # a private (0700) directory
    result = h.run(XORCISE_TS_DIR=str(link))
    assert result.returncode == 0, result.stderr
    h.wait_for_reaper()


def test_a_join_killed_mid_up_still_removes_the_key_file(harness):
    # A shell killed by a signal skips its EXIT trap unless the signal is turned into an exit.
    h = harness()
    proc = subprocess.Popen(
        ["/bin/sh"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env=h.env(XORCISE_STUB_UP_SECONDS="3"),
    )
    assert proc.stdin is not None
    proc.stdin.write(h.script())
    proc.stdin.close()
    deadline = time.monotonic() + 20
    while not (h.stub / "seen_key").exists():
        assert time.monotonic() < deadline, "`up` never started"
        time.sleep(0.05)
    assert list(h.parent.glob("*/authkey"))  # the key file exists while `up` runs
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) == 143
    assert not list(h.parent.glob("*/authkey"))


def test_a_client_that_predates_the_auth_key_spelling_still_joins_from_a_file(harness):
    h = harness(client="renamed")
    result = h.run()
    assert result.returncode == 0, result.stderr
    h.wait_for_reaper()
    log = h.argv_log()
    _assert_no_secret_in_any_argv(log)
    assert "--authkey=file:" in log  # the spelling this client documents
    assert (h.stub / "seen_key").read_text() == JOIN_KEY


def test_a_client_too_old_for_a_key_file_stops_before_the_key_is_used(harness):
    h = harness(client="nofile")
    result = h.run()
    assert result.returncode == 1
    assert "too old to read its auth key from a file" in result.stderr
    assert "XORCISE_TAILSCALE_CLIENT=fetched" in result.stderr
    assert not (h.stub / "seen_key").exists()  # `up` never ran
    log = h.argv_log()
    _assert_no_secret_in_any_argv(log)
    assert "tailscaled" not in log  # nothing was started
    assert "curl" not in log  # and nothing was fetched without being asked


def test_opting_into_the_fetched_client_runs_it_from_the_work_dir(harness):
    h = harness(client="nofile", serve_client=True)
    result = h.run(XORCISE_TAILSCALE_CLIENT="fetched")
    assert result.returncode == 0, result.stderr
    h.wait_for_reaper()

    _assert_no_secret_in_any_argv(h.argv_log())
    # The pinned client joined, from the scratch work dir — nothing was installed elsewhere.
    used = (h.stub / "seen_by").read_text()
    assert used.startswith(f"{h.parent}/xorcise-ts.")
    assert used.endswith(f"tailscale_{TAILSCALE_CLIENT_VERSION}_{_arch()}/tailscale")
    assert (h.stub / "seen_key").read_text() == JOIN_KEY
    # The tarball fetch authenticated with the bearer — over stdin, never on curl's argv.
    assert f'header = "Authorization: Bearer {BEARER}"' in (h.stub / "curl_stdin").read_text()


def test_an_unknown_client_choice_is_refused(harness):
    h = harness()
    result = h.run(XORCISE_TAILSCALE_CLIENT="system")
    assert result.returncode == 1
    assert "invalid XORCISE_TAILSCALE_CLIENT=system" in result.stderr


def test_the_stub_harness_would_catch_a_leak(harness, tmp_path):
    # Guard against a vacuous pass: the same harness, run over the pre-#172 shape of the join
    # (the key as a flag value), must see the key in the recorded argv.
    h = harness()
    leaky = h.bin / "leaky.sh"
    # `env` is a real host tool, so this also proves the PATH-wide wrappers are in the way.
    leaky.write_text(f'env tailscale up --authkey="{JOIN_KEY}" || true\n')
    subprocess.run(["/bin/sh", str(leaky)], env=h.env(), capture_output=True, timeout=30)
    with pytest.raises(AssertionError, match="reached an argv"):
        _assert_no_secret_in_any_argv(h.argv_log())
