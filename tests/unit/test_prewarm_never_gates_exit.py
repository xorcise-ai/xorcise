"""The nested-support pre-warm must never gate process exit.

`serve` runs its uvicorn servers under `asyncio.run()`. When the coroutine returns, asyncio's
Runner joins the DEFAULT executor before the process may exit — and a worker started by
`asyncio.to_thread()` lives in exactly that executor. The pre-warm probe is documented at
20-40 s, is deliberately fire-and-forget, and cannot be called back once it is inside the
blocking call: cancelling the task abandons the `await`, never the thread underneath it.

So the shutdown hook that cancels it returns instantly and proves nothing. Every listener
closed in ~0.3 s and the process then sat for another 6+ s waiting on a Docker probe nobody
was waiting for — which is how `xorcise serve` came to miss the 10 s teardown budget in
tests/e2e/test_walking_skeleton.py::test_up_serves_ui_then_down.

The probe belongs on a daemon thread, the idiom join_reconcile and terrain_catchup_v2 already
use for their own best-effort drains: nothing joins a daemon thread at exit, so abandoning the
warm-up costs exactly what its own docstring says it should — the memo stays cold and the
first run recomputes it.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from xorcise.core.config import get_settings
from xorcise.core.roles.boot.role_all import build_rest_app

# The real probe is documented at 20-40 s. A regression has to FAIL this test rather than hang
# the lane, so the stand-in blocks for a bounded window — comfortably past the budget asserted
# below, and released unconditionally in the `finally`.
_PROBE_BLOCK_SECONDS = 15.0
_EXIT_BUDGET_SECONDS = 5.0


def _startup_hook(app: Any, name: str) -> Callable[[], Awaitable[None]]:
    """The named startup handler, so this pins the pre-warm rather than the whole boot."""
    hook = next(h for h in app.router.on_startup if h.__name__ == name)
    return cast("Callable[[], Awaitable[None]]", hook)


def test_the_nested_prewarm_never_gates_process_exit(migrated_home, monkeypatch) -> None:
    # The probe is skipped under stubs or when the check is disabled, so both have to be off
    # for this test to exercise the path at all (asserted via `entered` below).
    monkeypatch.setenv("XORCISE_USE_STUBS", "0")
    monkeypatch.setenv("XORCISE_NESTED_CONTAINER_CHECK", "enforce")
    get_settings.cache_clear()

    entered = threading.Event()
    release = threading.Event()
    ran_on: dict[str, threading.Thread] = {}

    def _blocking_probe(_settings: object, _client_factory: object) -> None:
        ran_on["thread"] = threading.current_thread()
        entered.set()
        release.wait(_PROBE_BLOCK_SECONDS)

    monkeypatch.setattr("xorcise.core.rest.docker_runtime.prewarm_nested_support", _blocking_probe)

    app = build_rest_app()

    async def _lifespan() -> None:
        await _startup_hook(app, "_prewarm_nested_support")()
        # The bug only bites once the worker is INSIDE the blocking call, where cancellation
        # cannot reach it. Wait for that, or the test could pass by winning a race.
        for _ in range(50):
            if entered.is_set():
                break
            await asyncio.sleep(0.1)

    try:
        start = time.monotonic()
        # Not `await`: the whole point is the Runner teardown that `asyncio.run` performs on
        # the way out, which is where the default executor gets joined.
        asyncio.run(_lifespan())
        elapsed = time.monotonic() - start
    finally:
        release.set()

    assert entered.is_set(), "the pre-warm never ran — this test would prove nothing"
    assert elapsed < _EXIT_BUDGET_SECONDS, (
        f"asyncio.run() took {elapsed:.1f}s to unwind. The pre-warm probe is on the default "
        "executor, so process exit blocks on a 20-40 s Docker probe that nothing awaits."
    )
    assert ran_on["thread"].daemon, (
        "the pre-warm must run on a daemon thread: a non-daemon worker is joined at "
        "interpreter exit, which is the same hang arriving by a different route"
    )
