"""`up`'s update-available check — fetch, cache, comparison, and the notice gate.

Guarantees: every failure mode (offline, 404 = not yet published, malformed
JSON, corrupt cache) resolves to "no notice" rather than an error; the network
is hit at most once per TTL; source checkouts (+local version) are never
nagged; and the whole check is inert for non-TTY callers and opt-outs.

Monkeypatches of modules imported by `_update` (httpx) use the string target
form so they don't trip mypy's no-implicit-reexport on `_update.<mod>`.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

from xorcise.core.cli import _update
from xorcise.core.cli._shared import XORCISE_THEME

pytestmark = pytest.mark.unit

_UP = "xorcise.core.cli._update"


# --- _fetch_latest ---


def _resp(status: int = 200, payload: object = None):
    return SimpleNamespace(status_code=status, json=lambda: payload)


def test_fetch_latest_reads_pypi_version(monkeypatch) -> None:
    monkeypatch.setattr(
        f"{_UP}.httpx.get", lambda url, **kw: _resp(200, {"info": {"version": "1.2.3"}})
    )
    assert _update._fetch_latest() == "1.2.3"


def test_fetch_latest_not_yet_published_is_none(monkeypatch) -> None:
    monkeypatch.setattr(f"{_UP}.httpx.get", lambda url, **kw: _resp(404, {"message": "Not Found"}))
    assert _update._fetch_latest() is None


def test_fetch_latest_offline_is_none(monkeypatch) -> None:
    def _raise(url, **kw):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(f"{_UP}.httpx.get", _raise)
    assert _update._fetch_latest() is None


def test_fetch_latest_malformed_payload_is_none(monkeypatch) -> None:
    monkeypatch.setattr(f"{_UP}.httpx.get", lambda url, **kw: _resp(200, ["not", "a", "dict"]))
    assert _update._fetch_latest() is None
    monkeypatch.setattr(f"{_UP}.httpx.get", lambda url, **kw: _resp(200, {"info": {"version": 7}}))
    assert _update._fetch_latest() is None


# --- latest_published_version: the once-per-TTL cache ---


@pytest.fixture
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    return tmp_path


def test_cache_prevents_a_second_fetch(_home, monkeypatch) -> None:
    fetches: list[int] = []

    def _fetch() -> str:
        fetches.append(1)
        return "2.0.0"

    monkeypatch.setattr(_update, "_fetch_latest", _fetch)
    assert _update.latest_published_version(now=1000.0) == "2.0.0"
    assert _update.latest_published_version(now=1000.0 + 60) == "2.0.0"
    assert len(fetches) == 1, "a fresh cache must answer without the network"


def test_cache_expires_after_ttl(_home, monkeypatch) -> None:
    fetches: list[int] = []

    def _fetch() -> str:
        fetches.append(1)
        return "2.0.0"

    monkeypatch.setattr(_update, "_fetch_latest", _fetch)
    _update.latest_published_version(now=1000.0)
    _update.latest_published_version(now=1000.0 + _update._CACHE_TTL_SECONDS + 1)
    assert len(fetches) == 2


def test_failed_fetch_is_cached_too(_home, monkeypatch) -> None:
    """An offline host pays the attempt once per TTL, not once per `up`."""
    fetches: list[int] = []

    def _fetch() -> None:
        fetches.append(1)
        return None

    monkeypatch.setattr(_update, "_fetch_latest", _fetch)
    assert _update.latest_published_version(now=1000.0) is None
    assert _update.latest_published_version(now=1000.0 + 60) is None
    assert len(fetches) == 1


def test_corrupt_cache_refetches_instead_of_raising(_home, monkeypatch) -> None:
    (_home / "update-check.json").write_text("{not json")
    monkeypatch.setattr(_update, "_fetch_latest", lambda: "3.0.0")
    assert _update.latest_published_version(now=1000.0) == "3.0.0"


def test_future_timestamped_cache_reads_as_stale(_home, monkeypatch) -> None:
    """Clock skew must not freeze a bogus answer in place forever."""
    (_home / "update-check.json").write_text('{"checked_at": 99999999, "latest": "0.0.1"}')
    monkeypatch.setattr(_update, "_fetch_latest", lambda: "3.0.0")
    assert _update.latest_published_version(now=1000.0) == "3.0.0"


# --- update_notice ---


def test_notice_when_newer() -> None:
    notice = _update.update_notice("0.1.0", "0.2.0")
    assert notice is not None
    assert "v0.2.0" in notice
    assert "pip install -U xorcise" in notice


def test_no_notice_when_equal_or_older() -> None:
    assert _update.update_notice("0.2.0", "0.2.0") is None
    assert _update.update_notice("0.2.0", "0.1.0") is None
    assert _update.update_notice("0.2.0", None) is None


def test_source_checkouts_are_never_nagged() -> None:
    # setuptools-scm local segment (+g<sha>) = running from git; pip can't update that.
    assert _update.update_notice("0.0.2.dev1504+gf1590789", "0.0.2") is None


def test_published_dev_prerelease_is_upgradable() -> None:
    # A pip-installed dev pre-release (no local segment) upgrades via pip like any other.
    assert _update.update_notice("0.0.2.dev5", "0.0.2") is not None


def test_unparsable_versions_are_silent() -> None:
    assert _update.update_notice("not-a-version", "0.2.0") is None
    assert _update.update_notice("0.1.0", "not-a-version") is None


# --- begin_update_check: the gate ---


def _terminal_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=True, theme=XORCISE_THEME, soft_wrap=True)


def _boom() -> str:  # pragma: no cover - only hit on a regression
    raise AssertionError("the check must not run when gated off")


def test_non_tty_never_checks(monkeypatch) -> None:
    monkeypatch.setattr(_update, "latest_published_version", _boom)
    assert _update.begin_update_check()() is None  # shared console is not a TTY under pytest


def test_opt_out_env_never_checks(monkeypatch) -> None:
    monkeypatch.setattr(_update, "console", _terminal_console())
    monkeypatch.setattr(_update, "latest_published_version", _boom)
    monkeypatch.setenv("XORCISE_NO_UPDATE_CHECK", "1")
    assert _update.begin_update_check()() is None


def test_tty_check_yields_the_notice(monkeypatch) -> None:
    monkeypatch.setattr(_update, "console", _terminal_console())
    monkeypatch.setattr(_update, "latest_published_version", lambda: "999.0.0")
    monkeypatch.setattr(_update, "__version__", "0.1.0")
    notice = _update.begin_update_check()()
    assert notice is not None
    assert "999.0.0" in notice
