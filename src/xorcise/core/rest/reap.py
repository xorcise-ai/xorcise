"""Per-run container reaping (delivery/rest layer).

`xorcise down` calls this to remove every xorcise-managed per-run container. It is label-driven
(see `runner.docker.MANAGED_LABEL`), so it reaps orphans no in-process state knows about: an
abandoned run (the agent crashed before terminal), a server that was killed, and leftovers from
prior sessions. Reuses the real-vs-stub driver selection; on a non-Docker host or with
stubs forced it is a no-op (nothing to reap), and an unreachable daemon never fails the teardown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xorcise.core.config import Settings

if TYPE_CHECKING:
    from xorcise.core.runner.docker import DockerDriver


def reap_managed_containers(settings: Settings, *, driver: DockerDriver | None = None) -> list[str]:
    """Remove all xorcise-managed per-run containers; return their names. Safe to call any time."""
    if driver is None:
        from xorcise.core.rest.mission_pull import _real_docker_driver, _use_real_docker

        if not _use_real_docker(settings):
            return []  # stubs forced or a non-Docker role — no managed containers to reap
        try:
            driver = _real_docker_driver()
        except RuntimeError:
            return []  # Docker absent/unreachable — nothing to reap; never fail the teardown
    return driver.reap_managed()
