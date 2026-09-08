"""The app's startup singletons must be created ONCE per app, however many listeners share it.

`serve` used to build one uvicorn.Server per BIND HOST and pass them all the SAME app object:

    servers = [uvicorn.Server(uvicorn.Config(s.app, host=h, ...))
               for s in specs for h in _bind_hosts(s.host or host)]

role_all's agent-facing specs bind ('127.0.0.1', '::1') — plus the docker bridge gateway on
Linux — so the REST app got two or three servers and every `@app.on_event("startup")` hook fired
that many times. Observed live: two ReadinessWatchdog instances scanning the same runs, both
closing out the same run 3.6 ms apart, the loser failing its teardown with 409 "removal of
container ... is already in progress". Because each hook assigns its instance to a `nonlocal`,
the last one overwrote the others — so shutdown stopped only one and the rest kept ticking until
the process exited.

`serve` now runs one Server per app (see commands/serve.py), which fires each hook once. The
guards pinned here stay regardless: they hold under ANY ASGI host arrangement, which role_all
cannot see.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _count_startup_constructions(monkeypatch, migrated_home, fire: int) -> dict[str, int]:
    """Build the role_all app, fire its startup hooks `fire` times, count singletons made."""
    import xorcise.core.rest.budget_watchdog as bw
    import xorcise.core.rest.run_readiness as rr
    from xorcise.core.roles.boot import role_all

    counts = {"budget": 0, "readiness": 0, "reconcile": 0}

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

    def _counting_reconcile() -> None:
        counts["reconcile"] += 1

    monkeypatch.setattr(bw, "BudgetWatchdog", _CountingBudget)
    monkeypatch.setattr(rr, "ReadinessWatchdog", _CountingReadiness)
    monkeypatch.setattr("xorcise.core.rest.reconcile.reconcile_all_on_startup", _counting_reconcile)

    app = role_all.build_rest_app()
    handlers = app.router.on_startup
    import asyncio

    async def _fire_all() -> None:
        # CONCURRENTLY, because that is what uvicorn does when several Servers share an app:
        # each starts as its own asyncio task, so their startup hooks interleave at every await
        # point. Firing them sequentially hides check-then-act races — a guard that reads a
        # nonlocal, awaits, then assigns passes a sequential test and still double-starts here.
        await asyncio.gather(*(h() for _ in range(fire) for h in handlers))
        # Let the reconcile task (create_task → to_thread inside the hook) actually run.
        await asyncio.sleep(0.2)

    asyncio.run(_fire_all())
    return counts


def test_one_listener_creates_one_of_each(monkeypatch, migrated_home):
    counts = _count_startup_constructions(monkeypatch, migrated_home, fire=1)
    assert counts == {"budget": 1, "readiness": 1, "reconcile": 1}


@pytest.mark.parametrize("listeners", [2, 3])
def test_extra_listeners_still_create_only_one_of_each(monkeypatch, migrated_home, listeners):
    """The regression. Several listeners share one app, so startup fires several times (three
    on Linux: loopback, ::1, docker bridge gateway).

    Two ReadinessWatchdogs is not merely redundant: both scan the same runs and both act on the
    same verdict, which is how one run gets closed out twice and one teardown loses a 409.
    """
    counts = _count_startup_constructions(monkeypatch, migrated_home, fire=listeners)
    assert counts == {"budget": 1, "readiness": 1, "reconcile": 1}, (
        f"startup ran {listeners}x (one uvicorn.Server per bind host, same app) and built "
        f"duplicate background singletons: {counts}"
    )


def test_a_lifespan_restart_builds_fresh_singletons(monkeypatch, migrated_home):
    """Review of #81: the shutdown hooks left every guard claimed, so a startup → shutdown →
    startup on the same app object (two TestClient blocks, a host cycling the lifespan) came back
    with a STOPPED watchdog, no readiness gate and no reconcile — silently. Each shutdown hook now
    releases its guard."""
    import asyncio

    import xorcise.core.rest.budget_watchdog as bw
    import xorcise.core.rest.run_readiness as rr
    from xorcise.core.roles.boot import role_all

    counts = {"budget": 0, "readiness": 0, "reconcile": 0}

    class _Budget(bw.BudgetWatchdog):
        def __init__(self, *a, **k):
            counts["budget"] += 1
            super().__init__(*a, **k)

        def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    class _Readiness(rr.ReadinessWatchdog):
        def __init__(self, *a, **k):
            counts["readiness"] += 1
            super().__init__(*a, **k)

        def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    monkeypatch.setattr(bw, "BudgetWatchdog", _Budget)
    monkeypatch.setattr(rr, "ReadinessWatchdog", _Readiness)
    monkeypatch.setattr(
        "xorcise.core.rest.reconcile.reconcile_all_on_startup",
        lambda: counts.__setitem__("reconcile", counts["reconcile"] + 1),
    )
    app = role_all.build_rest_app()

    async def cycle() -> None:
        for h in app.router.on_startup:
            await h()
        await asyncio.sleep(0.2)
        for h in app.router.on_shutdown:
            await h()

    asyncio.run(cycle())
    asyncio.run(cycle())
    assert counts == {"budget": 2, "readiness": 2, "reconcile": 2}
