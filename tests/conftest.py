"""Shared pytest fixtures/config for all lanes.

Auto-marks every test with its lane marker based on the directory under tests/
(tests/unit/* -> @pytest.mark.unit, etc.) so the CI lanes (pytest -m <lane>)
always cover all tests in that lane — no per-file marker maintenance.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Keep `--help` output PLAIN, so help-text assertions read the words and not the colours.
#
# Typer forces Rich's terminal mode whenever GITHUB_ACTIONS is set (typer/rich_utils.py:
# `FORCE_TERMINAL = True if getenv("GITHUB_ACTIONS") or ...`). Colour then splits every flag
# across escape sequences — `--role` renders as `ESC[…m-ESC[0mESC[…m-roleESC[0m` — so
# `"--role" in result.output` is False on CI and True everywhere else. It cost a red CI run
# on a commit whose full suite was green locally.
#
# `_TYPER_FORCE_DISABLE_TERMINAL` is Typer's own escape hatch, and NO_COLOR / TERM=dumb /
# FORCE_COLOR=0 do NOT work — this is Typer forcing the terminal, not Rich choosing colour.
# It is read at IMPORT time into a module constant, so an autouse fixture would run far too
# late; it has to be set before anything imports typer.rich_utils. conftest is imported
# before any test module, and nothing above imports typer, so here is early enough.
# setdefault, so an operator debugging colour rendering can still override it.
os.environ.setdefault("_TYPER_FORCE_DISABLE_TERMINAL", "1")

_LANES = {"unit", "adapters", "topology", "integration", "e2e"}


@pytest.fixture
def real_headscale_unshared():
    """FAIL LOUDLY if the target Headscale already has a live agent run.

    The real-Headscale tests apply a full `policy set` replace; if a XORCISE dev env is using
    the same control plane, running them would clobber its ACL. Rather than clobber silently
    (or skip silently), refuse to run and tell the operator. Run-creating real tests depend on it.
    """
    from tests._helpers import stray_agent_nodes

    container = os.environ.get("XORCISE_HEADSCALE_CONTAINER", "headscale")
    stray = stray_agent_nodes(container)
    if stray:
        pytest.fail(
            f"Target Headscale '{container}' already has live agent node(s) {stray}: a "
            "XORCISE dev env is using this control plane. Run 'xorcise down' or point the "
            "real tests at an isolated container (XORCISE_HEADSCALE_CONTAINER) before running.",
            pytrace=False,
        )
    yield


@pytest.fixture(autouse=True)
def _restore_xorcise_env():
    """Undo XORCISE_* env writes made by production code under test.

    monkeypatch only restores what IT changed: `delenv(name, raising=False)` on an
    absent var records nothing, so a command that then WRITES that var (e.g. `up`
    stamping XORCISE_REST_PORT after relocating a busy port) leaks it into every
    later test. Snapshot the whole XORCISE_* namespace and restore it afterwards.
    """
    before = {k: v for k, v in os.environ.items() if k.startswith("XORCISE_")}
    yield
    for key in [k for k in os.environ if k.startswith("XORCISE_")]:
        if key not in before:
            del os.environ[key]
    os.environ.update(before)


@pytest.fixture(autouse=True)
def _force_stubs(monkeypatch):
    """Keep the test suite Docker-free: default to stub adapters.

    Real-by-default selects the real DockerSdkDriver on role all/runner, which needs a
    daemon. Tests run with XORCISE_USE_STUBS=1 so the running-server paths stay hermetic;
    a test exercising the REAL selection delenv's this and sets the role explicitly.
    """
    monkeypatch.setenv("XORCISE_USE_STUBS", "1")
    # Production deliberately drains late OTLP for a few seconds before sealing. The broad test
    # suite uses the explicit zero-delay mode; targeted lifecycle tests exercise the wait itself.
    monkeypatch.setenv("XORCISE_TELEMETRY_DRAIN_SECONDS", "0")


@pytest.fixture(autouse=True)
def _skip_frontend_build(monkeypatch):
    """Keep the suite npm-free: `xorcise up` tests must not shell out to `npm run build:static`.

    ensure_frontend_ready rebuilds the /ui export when it looks stale; in a test that
    would run a real build (slow) and trip a mocked subprocess.Popen. Skip it globally — the few
    tests that exercise the build path opt back in by delenv-ing this in the test body.
    """
    monkeypatch.setenv("XORCISE_SKIP_FRONTEND_BUILD", "1")


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Isolate the cached Settings between tests.

    get_settings() is an lru_cache; a test that builds a Settings under custom
    XORCISE_* env leaves that instance cached after monkeypatch tears the env
    down, so the next test reads a stale config. Clear the cache before and
    after every test so no settings state leaks across tests.
    """
    from xorcise.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        parts = Path(str(item.path)).parts
        if "tests" in parts:
            i = parts.index("tests")
            if i + 1 < len(parts) and parts[i + 1] in _LANES:
                item.add_marker(getattr(pytest.mark, parts[i + 1]))


@pytest.fixture(scope="session")
def _migrated_db_template(tmp_path_factory) -> Path:
    """Migrate ONE throwaway SQLite DB to head for the whole session (per xdist worker).

    `migrated_home` copies this file per test instead of replaying the migration chain each time.
    Replaying was ~1.65s × the (growing) chain per test and dominated the unit lane; a file copy is
    ~ms and constant regardless of migration count. The migration chain itself is still exercised
    directly by the migration tests (test_db_migrate / test_otel_traces_migration), which call
    `db.upgrade()` on their own throwaway homes — so real-migration coverage is unchanged.
    """
    from xorcise.core import config, db

    home = tmp_path_factory.mktemp("db-template")
    prev_home = os.environ.get("XORCISE_HOME")
    os.environ["XORCISE_HOME"] = str(home)
    try:
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.upgrade()  # full alembic chain -> head, ONCE
        db.get_engine().dispose()  # flush + close so the file is safe to copy
    finally:
        if prev_home is None:
            os.environ.pop("XORCISE_HOME", None)
        else:
            os.environ["XORCISE_HOME"] = prev_home
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
    return Path(home) / "xorcise.db"


@pytest.fixture
def migrated_home(tmp_path, monkeypatch, _migrated_db_template):
    """A throwaway XORCISE_HOME with the schema migrated. Yields the home path.

    The schema is copied from the session-migrated template DB (`_migrated_db_template`) rather than
    re-run per test — see that fixture for why. End state is identical to `db.upgrade()`: an empty
    DB at head (alembic_version stamped), so a test that calls `db.upgrade()` again is a no-op."""
    import shutil

    from xorcise.core import config, db

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    shutil.copyfile(_migrated_db_template, tmp_path / "xorcise.db")
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    yield tmp_path
    db.get_engine().dispose()
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()


@pytest.fixture
def host_probes_ok(monkeypatch):
    """Treat every HOST prerequisite as satisfied, for tests about something else.

    `doctor` probes the real host (docker, the Compose v2 plugin, openssl,
    /dev/net/tun, disk). A test asserting on ports or the data directory must not
    depend on what the runner happens to have installed — CI has no Compose
    plugin, so unpatched probes turned those tests red while they passed locally.
    Stub the whole set here; a test then overrides only the probe it is about."""
    from xorcise.core.cli._diagnostics import Check
    from xorcise.core.cli.commands import lifecycle

    ok = {
        "python_version": Check("python", True, "Python 3.12.0", level="warning"),
        "docker_present": Check("docker", True, "present"),
        "docker_daemon": Check("docker", True, "ok"),
        "docker_compose_v2": Check("docker compose", True, "ok"),
        "openssl_present": Check("openssl", True, "present"),
        "disk_space": Check("disk space", True, "999 GB free", level="warning"),
        "tun_device": Check("/dev/net/tun", True, "present", level="warning"),
    }
    for name, check in ok.items():
        monkeypatch.setattr(lifecycle, name, lambda c=check: c)
    # Takes the container name, and only runs when doctor believes XORCISE is up — so
    # it needs its own arg-tolerant stub. Unstubbed it would `docker exec` the real
    # host, passing on a developer machine with a live control plane and failing on CI.
    plane = Check("control plane", True, "'headscale' reachable")
    monkeypatch.setattr(lifecycle, "control_plane", lambda *a, **k: plane)
    ok["control_plane"] = plane
    return ok
