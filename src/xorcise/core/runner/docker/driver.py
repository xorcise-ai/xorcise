"""Real DockerDriver via the docker SDK (runner extra). The ONLY Docker-touching path.

`docker` is imported lazily in __init__ so the module imports cleanly in roles that lack
the `runner` extra; only constructing the driver requires the SDK + a daemon.
"""

from __future__ import annotations

import json as _json
from collections.abc import Callable
from typing import Any

from xorcise.core.runner.docker import (
    MANAGED_LABEL,
    RUN_ID_LABEL,
    ContainerHandle,
    ContainerSpec,
    ContainerState,
    DockerDriver,
    PullProgress,
    ServiceState,
)


def _parse_compose_ps(output: bytes | str) -> tuple[ServiceState, ...]:
    """Parse `docker compose ps --format json` into ServiceStates (tolerant, never raises).

    Compose emits NDJSON (one object per line) on modern versions and a single JSON array on
    others; anything unparseable yields () so the caller reads it as "unknown" and degrades to
    ready rather than wedging a healthy run at PENDING."""
    text = output.decode(errors="replace") if isinstance(output, bytes) else output
    rows: list[Any] = []
    stripped = text.strip()
    if not stripped:
        return ()
    try:  # array form
        parsed = _json.loads(stripped)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except ValueError:  # NDJSON form — parse per line, skipping noise
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(_json.loads(line))
            except ValueError:
                continue
    states = [
        ServiceState(
            name=str(r.get("Service") or r.get("Name") or ""),
            status=str(r.get("State", "")),
        )
        for r in rows
        if isinstance(r, dict)
    ]
    return tuple(s for s in states if s.name)


def _split_ref(image: str) -> tuple[str, str | None]:
    """Split an OCI ref into (repository, tag). docker-py's images.pull pulls ALL tags when
    tag is None, so a ref like host/repo:tag must be split — but only on a ':' that follows the
    last '/' (a registry-host port has a ':' before the first '/')."""
    last_segment = image.rsplit("/", 1)[-1]
    if ":" in last_segment:
        repo, tag = image.rsplit(":", 1)
        return repo, tag
    return image, None


class DockerSdkDriver(DockerDriver):
    def __init__(self, client: Any = None, *, platform: str = "linux/amd64") -> None:
        # `docker` is imported lazily so the module imports cleanly without the runner extra;
        # tests inject a fake `client` to exercise pull wiring without a daemon.
        # `platform` is passed to pull + run so amd64-only mission images resolve on an
        # arm64 host (Apple Silicon) instead of 404-ing on a missing arm64 manifest; "" ⇒ let docker
        # pick the host platform (the previous behavior).
        if client is None:
            import docker  # type: ignore[import-untyped]  # only the runner extra ships it

            client = docker.from_env()
        self._client = client
        self._platform = platform

    def pull(
        self,
        image: str,
        *,
        auth: tuple[str, str] | None = None,
        progress: Callable[[PullProgress], None] | None = None,
    ) -> None:
        repo, tag = _split_ref(image)
        auth_config = {"username": auth[0], "password": auth[1]} if auth else None
        # Low-level streaming pull: the high-level images.pull silently consumes docker's
        # per-layer progressDetail events, which are the only source of byte-level progress.
        # decode=True yields dicts; an "error" event is how the daemon reports pull failure.
        # platform rides along so amd64-only images still resolve on arm64 hosts.
        for evt in self._client.api.pull(
            repo,
            tag=tag,
            platform=self._platform or None,
            auth_config=auth_config,
            stream=True,
            decode=True,
        ):
            error = evt.get("error")
            if error:
                raise RuntimeError(str(error))  # wrapped into PullError by the pull spine
            if progress is None:
                continue
            # Status-only events (no progressDetail) used to be dropped here. They are the ONLY
            # signal for the phases that carry no bytes — "Pulling fs layer", "Waiting" and,
            # crucially, "Already exists" for a layer already in the local store — so dropping them
            # left the caller unable to tell a healthy cached/negotiating pull from a dead one, and
            # it reported "Downloading… 0 B" throughout. Forward them with zeroed counts and let the
            # pull spine decide what the phase is.
            detail = evt.get("progressDetail") or {}
            progress(
                PullProgress(
                    layer_id=str(evt.get("id") or ""),
                    status=str(evt.get("status") or ""),
                    current=int(detail.get("current") or 0),
                    total=int(detail.get("total") or 0),
                )
            )

    def image_exists(self, image: str) -> bool:
        import docker.errors  # type: ignore[import-untyped]

        try:
            self._client.images.get(image)
            return True
        except docker.errors.ImageNotFound:
            return False

    def image_labels(self, image: str) -> dict[str, str] | None:
        """The image's config labels (inherited included), or None if it is not present."""
        try:
            img = self._client.images.get(image)
        except Exception:  # noqa: BLE001 — absent/unreadable image ⇒ "no labels", never a crash
            return None
        return dict((img.attrs.get("Config") or {}).get("Labels") or {})

    def run(self, spec: ContainerSpec) -> ContainerHandle:
        # The fused image is local-only (no registry). containers.run auto-PULLS a missing image,
        # which for a local-only ref fails with a cryptic "pull access denied" — so fail loud
        # first if it isn't in the local store, telling the caller to (re)build it.
        if not self.image_exists(spec.image):
            from xorcise.core.contracts.errors import ImageNotInstalledError

            raise ImageNotInstalledError(f"image {spec.image!r} is not in the local store")
        # Fused image runs an inner dockerd → privileged + /dev/net/tun for Tailscale.
        # The managed label lets `xorcise down` reap this container without any
        # in-process bookkeeping — even after a server restart or an abandoned run.
        #
        # The host's Docker socket is deliberately NOT mounted. Doing so used to make the
        # entrypoint compose the mission stack as SIBLINGS on the operator's own daemon, which
        # put every run's containers into one shared namespace: parallel runs collided on the
        # missions' fixed container_names and published host ports, teardown leaked un-labelled
        # containers, and mission bind mounts resolved against the host filesystem. The stack now
        # always runs on the inner daemon. The entrypoint still branches on the socket's presence,
        # so not mounting it is what selects DinD.
        container = self._client.containers.run(
            spec.image,
            name=spec.name,
            detach=True,
            privileged=True,
            devices=["/dev/net/tun:/dev/net/tun"],
            environment=dict(spec.env),
            labels={MANAGED_LABEL: "true", RUN_ID_LABEL: spec.name},
            platform=self._platform or None,  # run the amd64 image on an arm64 host
        )
        return ContainerHandle(container_id=container.id, image=spec.image)

    def _remove_run_resources(self, run_id: str) -> None:
        """Remove the run's outer lifecycle container, then any host-daemon leftovers it owns.
        Idempotent.

        Removing the outer container is now sufficient for a run's OWN stack: the mission
        containers live on its inner daemon and die with it. The host-daemon sweeps below are
        kept deliberately — they are the only thing that cleans up runs created BEFORE the
        sibling topology was removed, and they cost one filtered list call against labels no
        DinD-era run sets. Deleting them would strand those containers forever, since nothing
        else knows they exist."""
        import contextlib

        from docker.errors import NotFound

        for container in self._client.containers.list(
            all=True, filters={"label": f"{RUN_ID_LABEL}={run_id}"}
        ):
            container.remove(force=True)
        # Legacy sibling-era cleanup. Compose networks/volumes carry its standard project label.
        # A network cannot be removed while any container is still ATTACHED, and sibling mission
        # containers carried ONLY the compose-project label, not our run-id label — so the sweep
        # above missed them and a bare network.remove() 403s ("has active endpoints").
        # Force-remove whatever the network still holds, by ATTACHMENT (labels can't be relied
        # on), before removing it — this is the e397d231 teardown leak.
        for network in self._client.networks.list(
            filters={"label": f"com.docker.compose.project={run_id}"}
        ):
            network.reload()  # inspect so .attrs["Containers"] reflects the live endpoints
            for cid in network.attrs.get("Containers") or {}:
                # already gone between the inspect and the remove ⇒ idempotent
                with contextlib.suppress(NotFound):
                    self._client.containers.get(cid).remove(force=True)
            network.remove()
        for volume in self._client.volumes.list(
            filters={"label": f"com.docker.compose.project={run_id}"}
        ):
            volume.remove(force=True)

    def stop(self, container_id: str) -> None:
        container = self._client.containers.get(container_id)
        run_id = (container.labels or {}).get(RUN_ID_LABEL)
        if run_id:
            self._remove_run_resources(run_id)
        else:
            container.remove(force=True)

    def stop_by_name(self, name: str) -> bool:
        # stop by the deterministic container name (== run_id) so teardown works
        # without an in-memory handle (after a restart / from a second process).
        #
        # The resource sweep runs UNCONDITIONALLY, not only when the container is still there. The
        # outer container exits on its own when its entrypoint fails, and once it had been reaped
        # this returned early and removed NOTHING — leaking the compose network that holds the run's
        # /24 forever (the orphan behind the observed subnet collision). Removing resources is
        # idempotent, so doing it either way is safe; the bool still reports whether a container was
        # actually there to stop.
        from docker.errors import NotFound

        try:
            self._client.containers.get(name)
            existed = True
        except NotFound:
            existed = False
        self._remove_run_resources(name)
        return existed

    def list_compose_projects(self) -> set[str]:
        """Every compose project that still holds a network on this daemon.

        Compose labels the networks it creates with its project name (== the run id for a run's
        stack), so this surfaces environments whose network outlived their run."""
        projects: set[str] = set()
        for network in self._client.networks.list():
            labels = (getattr(network, "attrs", None) or {}).get("Labels") or {}
            project = labels.get("com.docker.compose.project")
            if project:
                projects.add(str(project))
        return projects

    def remove_run_resources(self, run_id: str) -> None:
        """Release a run's containers, networks and volumes by project. Idempotent."""
        self._remove_run_resources(run_id)

    def inspect_by_name(self, name: str) -> ContainerHandle | None:
        # report the live container's identity by its deterministic name
        # (== run_id) so deploy-state survives a restart / a second process. containers.get accepts
        # a name; NotFound ⇒ absent. Image tag is best-effort (the server has sandbox_ref durably).
        from docker.errors import NotFound

        try:
            c = self._client.containers.get(name)
        except NotFound:
            return None
        tags = getattr(c.image, "tags", None) or []
        return ContainerHandle(container_id=c.id, image=tags[0] if tags else "")

    def container_state(self, name: str) -> ContainerState | None:
        # docker returns an EXITED container from containers.get just like a running one, and
        # ContainerHandle carries no state — so read status + exit code explicitly here.
        from docker.errors import NotFound

        try:
            c = self._client.containers.get(name)
        except NotFound:
            return None
        code = ((getattr(c, "attrs", None) or {}).get("State") or {}).get("ExitCode")
        return ContainerState(
            status=str(getattr(c, "status", "") or ""),
            exit_code=code if isinstance(code, int) else None,
        )

    def compose_service_states(self, project: str) -> tuple[ServiceState, ...]:
        # The mission stack runs INSIDE the outer container (named == run id == compose project),
        # so enumerate it with a nested `compose ps`. The exec inherits the container's env, and
        # therefore the same inner daemon the entrypoint composed against.
        from docker.errors import NotFound

        try:
            container = self._client.containers.get(project)
        except NotFound:
            return ()
        try:
            _code, output = container.exec_run(
                ["docker", "compose", "-p", project, "ps", "--format", "json"]
            )
        except Exception:  # noqa: BLE001 — unknown state must never break the caller's poll
            return ()
        return _parse_compose_ps(output)

    def list_network_cidrs(self) -> set[str]:
        # Every network's IPAM subnets, so the allocator can avoid a subnet a LEFTOVER run network
        # still holds (imperfect teardown). Read from network.attrs["IPAM"]["Config"] — host/none
        # networks carry no config; tolerate a missing/null/Subnet-less entry rather than crash.
        cidrs: set[str] = set()
        for network in self._client.networks.list():
            config = ((network.attrs or {}).get("IPAM") or {}).get("Config") or []
            for entry in config:
                subnet = (entry or {}).get("Subnet")
                if subnet:
                    cidrs.add(subnet)
        return cidrs

    def reap_managed(self) -> list[str]:
        # all=True: include exited/crashed containers (orphans from prior sessions), not just
        # the running ones — the leak the bug report saw included a 3-day-old exited container.
        managed = self._client.containers.list(all=True, filters={"label": f"{MANAGED_LABEL}=true"})
        run_ids: set[str] = set()
        for container in managed:
            run_id = (container.labels or {}).get(RUN_ID_LABEL)
            if isinstance(run_id, str):
                run_ids.add(run_id)
        reaped = [c.name for c in managed]
        for run_id in run_ids:
            self._remove_run_resources(run_id)
        return reaped
