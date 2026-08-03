"""Typed httpx client — the ONLY logic path in the CLI (cli).

Mirrors the REST contract; typed DTOs may arrive later (returns dicts now).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.markup import escape

from xorcise.core.cli._shared import err_console
from xorcise.core.cli._ux import command_path_from_argv
from xorcise.core.config import get_settings
from xorcise.core.home import pid_file, read_runtime_ports, xorcise_home

_SERVICE_DOWN = (
    "[err]error[/err]: cannot reach the XORCISE service — is it running?\n"
    "start it: [value]xorcise up[/value]\n"
    "check:    [value]xorcise status[/value]"
)

# Default read timeout for control calls. Long operations (e.g. a real image pull) pass a larger
# value per-call so the CLI waits instead of falsely reporting a timeout.
_DEFAULT_TIMEOUT_SECONDS = 5.0

# Homes we've already checked for a foreign-instance answer this process (warn once).
_FOREIGN_CHECKED: set[str] = set()


def default_base_url() -> str:
    s = get_settings()
    port = s.rest_port
    # XORCISE_REST_PORT on the invocation is a deliberate "talk to THIS service" instruction —
    # it stays the escape hatch and outranks discovery. Otherwise prefer the runtime record:
    # `up` may have auto-incremented off the configured port, and that record is where the
    # service actually bound. It is trusted only while the pid file says it is up.
    if "XORCISE_REST_PORT" not in os.environ and pid_file().exists():
        port = (read_runtime_ports() or {}).get("rest", port)
    return f"http://{s.host}:{port}/api"


def _warn_if_foreign_instance(base_url: str) -> None:
    """Once per (process, home): if NO instance was started from this home but a
    service is answering on the configured port, it belongs to a DIFFERENT home —
    a home-scoped read must not be silently answered by someone else's instance.

    Cheap by construction: the pid-file stat gates the probe, so the extra /system
    round-trip happens only in the suspicious state (no local pid file, yet a
    response came back), never on a normal `up`-started session."""
    if pid_file().exists():
        return
    home = str(xorcise_home())
    if home in _FOREIGN_CHECKED:
        return
    _FOREIGN_CHECKED.add(home)
    try:
        info = httpx.get(f"{base_url}/system", timeout=1).json()
    except (httpx.HTTPError, ValueError):
        return
    their_home = str(info.get("home") or "") if isinstance(info, dict) else ""
    if their_home and Path(their_home).resolve() != Path(home).resolve():
        err_console.print(
            "[warn]note: no XORCISE instance was started from this home — the responding "
            f"services belong to the instance at {their_home}[/warn]"
        )


class RestClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or default_base_url()

    def get(self, path: str, timeout: float | None = None) -> Any:
        t = timeout or _DEFAULT_TIMEOUT_SECONDS
        return self._call(lambda: httpx.get(f"{self.base_url}{path}", timeout=t), t, self.base_url)

    def get_run_result(self, run_id: str) -> Any:
        """A run's result envelope; a still-active run (the server 409s 'not terminal
        yet — no result') returns a soft ``{"status": "active"}`` so `run status` /
        `run report` render progress instead of a raw 409 that looks like an internal
        failure. Every other status is handled exactly like `get`."""
        t = _DEFAULT_TIMEOUT_SECONDS
        url = f"{self.base_url}/runs/{run_id}/result"
        try:
            resp = httpx.get(url, timeout=t)
        except httpx.HTTPError:
            # Re-issue through the shared handler so a connection/timeout error gets
            # the same clean, operation-aware message + exit (rare double request).
            return self._call(lambda: httpx.get(url, timeout=t), t, self.base_url)
        if resp.status_code == 409:
            detail = ""
            try:
                detail = str((resp.json() or {}).get("detail", ""))
            except ValueError:
                detail = resp.text
            if "not terminal" in detail or "no result" in detail:
                return {"status": "active"}
        return self._call(lambda: resp, t, self.base_url)

    def get_or_none(self, path: str, timeout: float | None = None) -> Any:
        """GET returning None on ANY failure (connection, timeout, error status,
        bad JSON) — for live-probe reads that degrade gracefully instead of
        exiting (e.g. the mission-library live status)."""
        t = timeout or _DEFAULT_TIMEOUT_SECONDS
        try:
            resp = httpx.get(f"{self.base_url}{path}", timeout=t)
            if resp.is_error or not resp.content:
                return None
            return resp.json()
        except (httpx.HTTPError, ValueError):
            return None

    def get_text(self, path: str, timeout: float | None = None) -> str:
        """GET a non-JSON body verbatim (run reports: text/markdown + text/html).

        The rest of this client decodes JSON; a report is a document, not a DTO. Error handling
        is shared with `_call` — an unreachable service or an error status still exits cleanly —
        only the success path differs (resp.text, not resp.json())."""
        t = timeout or _DEFAULT_TIMEOUT_SECONDS
        return self._call_text(
            lambda: httpx.get(f"{self.base_url}{path}", timeout=t), t, self.base_url
        )

    def post(self, path: str, json: dict[str, Any], timeout: float | None = None) -> Any:
        t = timeout or _DEFAULT_TIMEOUT_SECONDS
        return self._call(
            lambda: httpx.post(f"{self.base_url}{path}", json=json, timeout=t), t, self.base_url
        )

    def put(self, path: str, json: dict[str, Any], timeout: float | None = None) -> Any:
        t = timeout or _DEFAULT_TIMEOUT_SECONDS
        return self._call(
            lambda: httpx.put(f"{self.base_url}{path}", json=json, timeout=t), t, self.base_url
        )

    def delete(self, path: str, timeout: float | None = None) -> Any:
        t = timeout or _DEFAULT_TIMEOUT_SECONDS
        return self._call(
            lambda: httpx.delete(f"{self.base_url}{path}", timeout=t), t, self.base_url
        )

    @classmethod
    def _call(cls, send: Callable[[], httpx.Response], timeout: float, base_url: str = "") -> Any:
        """Send the request and decode the JSON body (None for an empty 204)."""
        resp = cls._send(send, timeout, base_url)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            # A 2xx with a non-JSON body means the contract broke — fail clean, not with
            # a JSONDecodeError traceback.
            err_console.print(
                "[err]invalid response from the XORCISE service[/err] — expected JSON"
            )
            raise typer.Exit(1) from exc

    @classmethod
    def _call_text(
        cls, send: Callable[[], httpx.Response], timeout: float, base_url: str = ""
    ) -> str:
        """Send the request and return the body verbatim — for text/markdown + text/html."""
        return cls._send(send, timeout, base_url).text

    @staticmethod
    def _send(
        send: Callable[[], httpx.Response], timeout: float, base_url: str = ""
    ) -> httpx.Response:
        """Send the request, turning an unreachable service or an error response into a clean
        CLI error. An error status must NOT be blindly .json()'d — a text/plain 500 would throw
        a JSONDecodeError; surface the server's `detail` (or the body) and exit non-zero.

        Guidance is operation-aware: the retry line names the command that actually ran
        (argv-derived), never an unrelated workflow's command."""
        try:
            resp = send()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            err_console.print(_SERVICE_DOWN)
            raise typer.Exit(1) from exc
        except httpx.TimeoutException as exc:
            # Read/pool timeout: the service is reachable but slow to answer THIS request.
            # Never a raw traceback, and never another workflow's command as the fix.
            err_console.print(
                f"[err]error[/err]: the XORCISE service did not respond "
                f"within {timeout:.0f} seconds"
            )
            err_console.print("check services: [value]xorcise status[/value]")
            err_console.print("diagnose:       [value]xorcise doctor[/value]")
            err_console.print(f"retry:          [value]{command_path_from_argv()}[/value]")
            raise typer.Exit(1) from exc
        except httpx.HTTPError as exc:
            # Anything else on the wire (protocol error, reset mid-body, bad redirect, …).
            err_console.print(
                f"[err]connection to the XORCISE service failed[/err] — {escape(str(exc))}"
            )
            raise typer.Exit(1) from exc
        if resp.is_error:
            detail = ""
            try:
                body = resp.json()
                if isinstance(body, dict):
                    detail = str(body.get("detail", ""))
            except ValueError:  # non-JSON error body (e.g. a bare text/plain 500)
                detail = resp.text.strip()
            # The detail is server-authored text — escape it so it can't inject markup.
            if 400 <= resp.status_code < 500 and detail:
                # A client-error status with a server-authored sentence ("no run 'x'",
                # "run '…' is not terminal yet") reads in the CLI's own voice — HTTP
                # jargon stays out of the headline, and it never looks like a crash.
                err_console.print(f"[err]error[/err]: {escape(detail)}")
            else:
                err_console.print(
                    f"[err]request failed ({resp.status_code})[/err]: "
                    f"{escape(detail or resp.reason_phrase)}"
                )
            raise typer.Exit(1)
        if base_url:
            _warn_if_foreign_instance(base_url)
        return resp
