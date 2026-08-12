"""The app's startup singletons must be created ONCE per app, not once per listener.

`serve` builds one uvicorn.Server per BIND HOST but passes them all the SAME app object:

    servers = [uvicorn.Server(uvicorn.Config(s.app, host=h, ...))
               for s in specs for h in _bind_hosts(s.host or host)]

role_all's agent-facing specs bind ('127.0.0.1', '::1'), so the REST app gets two servers and
every `@app.on_event("startup")` hook fires twice. Observed live: two ReadinessWatchdog
instances scanning the same runs, both closing out the same run 3.6 ms apart, the loser failing
its teardown with 409 "removal of container ... is already in progress". Because each hook
assigns its instance to a `nonlocal`, the second overwrites the first — so shutdown stops only
one and the other keeps ticking until the process exits.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _count_startup_constructions(monkeypatch, migrated_home, fire: int) -> dict[str, int]:
    """Build the role_all app, fire its startup hooks `fire` times, count singletons made."""
    import xorcise.core.rest.budget_watchdog as bw
    import xorcise.core.rest.run_readiness as rr
    from xorcise.core.roles.boot import role_all

    counts = {"budget": 0, "readiness": 0}

    class _CountingBudget(bw.BudgetWatchdog):
        def __init__(self, *a, **k):
            counts["budget"] += 1
            super().__init__(*a, **k)

        def start(self) -> None:  # never schedule real background work in a unit test
            pass

    class _CountingReadiness(rr.ReadinessWatchdog):
        def __init__(self, *a, **k):
            counts["readiness"] += 1
            super().__init__(*a, **k)

        def start(self) -> None:
            pass

    monkeypatch.setattr(bw, "BudgetWatchdog", _CountingBudget)
    monkeypatch.setattr(rr, "ReadinessWatchdog", _CountingReadiness)

    app = role_all.build_rest_app()
    handlers = app.router.on_startup
    import asyncio

    async def _fire_all() -> None:
        # CONCURRENTLY, because that is what uvicorn does: `serve` starts one Server per bind
        # address as separate asyncio tasks, so their startup hooks interleave at every await
        # point. Firing them sequentially hides check-then-act races — a guard that reads a
        # nonlocal, awaits, then assigns passes a sequential test and still double-starts here.
        await asyncio.gather(*(h() for _ in range(fire) for h in handlers))

    asyncio.run(_fire_all())
    return counts


def test_one_listener_creates_one_of_each(monkeypatch, migrated_home):
    counts = _count_startup_constructions(monkeypatch, migrated_home, fire=1)
    assert counts == {"budget": 1, "readiness": 1}


def test_two_listeners_still_create_only_one_of_each(monkeypatch, migrated_home):
    """The regression. Two listeners share one app, so startup fires twice.

    Two ReadinessWatchdogs is not merely redundant: both scan the same runs and both act on the
    same verdict, which is how one run gets closed out twice and one teardown loses a 409.
    """
    counts = _count_startup_constructions(monkeypatch, migrated_home, fire=2)
    assert counts == {"budget": 1, "readiness": 1}, (
        "startup ran twice (one uvicorn.Server per bind host, same app) and built duplicate "
        f"background singletons: {counts}"
    )
