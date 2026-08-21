"""DockerDriver port + stub impl — the ONLY code that talks Docker. LAYER:
PART-ISLAND (runner-internal). Its in-process types live HERE, not in contracts/
(they never cross the wire). The real docker-py driver lives in driver.py; the
stub records calls and pulls/runs nothing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

# Every per-run container the driver creates is stamped with this label so it can be reaped
# without any in-process bookkeeping — survives a server restart, an abandoned run,
# and prior sessions. RUN_ID_LABEL records which run a container belongs to (for diagnostics).
MANAGED_LABEL = "xorcise.managed"
RUN_ID_LABEL = "xorcise.run_id"


@dataclass(frozen=True)
class ContainerSpec:
    """In-process spec for a container the runner pulls + runs (image-pull model)."""

    image: str  # OCI image ref the runner pulls
    name: str
    env: tuple[tuple[str, str], ...] = ()
    # Explicit execution platform (AS5), e.g. "linux/arm64". None ⇒ the driver's default; with
    # an auto ("") driver default docker then runs the LOCAL image as-is — which is the selected
    # one by construction, because the pull was explicit.
    platform: str | None = None


@dataclass(frozen=True)
class ContainerHandle:
    container_id: str
    image: str


@dataclass(frozen=True)
class ContainerState:
    """Liveness of the OUTER per-run lifecycle container: docker's status + its exit code.

    `deploy()` only starts this container; the mission stack comes up asynchronously inside it, so
    the server needs its true liveness to tell a live run from one whose environment
    already died."""

    status: str  # docker's container status: "running", "exited", "created", …
    exit_code: int | None = None

    @property
    def exited(self) -> bool:
        """True once the container is no longer live. The outer container is meant to stay up for
        the whole run, so ANY exit before teardown is abnormal — including a clean exit(0)."""
        return self.status not in {"running", "restarting", "created", "paused"}


@dataclass(frozen=True)
class ServiceState:
    """One INNER mission-stack service (a compose service in the run's project)."""

    name: str
    status: str  # compose's state: "running", "created", "exited", …

    @property
    def running(self) -> bool:
        return self.status == "running"


@dataclass(frozen=True)
class PullProgress:
    """One docker layer-progress event (status/id/progressDetail) forwarded during a pull.

    `current`/`total` are bytes for this layer; `total` is 0 while docker hasn't sized the
    layer yet, so consumers must tolerate zero/growing totals."""

    layer_id: str
    status: str
    current: int
    total: int


class DockerDriver(ABC):
    @abstractmethod
    def pull(
        self,
        image: str,
        *,
        auth: tuple[str, str] | None = None,
        progress: Callable[[PullProgress], None] | None = None,
        platform: str | None = None,
    ) -> None:
        """Pull an image into the local store. `auth` is (username, password) for a private
        registry (e.g. a broker-minted ECR token); None ⇒ anonymous pull.
        `progress` (keyword-only, default None ⇒ backward-compatible for existing call sites)
        receives per-layer byte progress while the pull streams.
        `platform` (keyword-only, default None) is the explicit per-mission selection (AS5);
        None falls back to the driver's construction-time platform. The pull spine forwards it
        only when a selection was actually made, so pre-existing fakes keep working."""

    @abstractmethod
    def run(self, spec: ContainerSpec) -> ContainerHandle: ...

    @abstractmethod
    def stop(self, container_id: str) -> None: ...

    @abstractmethod
    def stop_by_name(self, name: str) -> bool:
        """Force-remove the container with this (run-id-derived) name; True iff one was removed.

        Keyed on the deterministic container name, not an in-memory handle, so teardown works
        across a server restart or from a second process. Absent ⇒ False."""

    # Whether this driver has an image store to read from at all. The stub does not; a real driver
    # does. The confinement pass keys off this to tell "nothing to read here" apart from "the read
    # failed", so a driver can only decline to confine by saying so explicitly.
    reads_image_files: ClassVar[bool] = False

    @abstractmethod
    def read_image_file(self, image: str, path: str) -> str | None:
        """A file's text from inside an image, WITHOUT running it. None ⇒ this driver can't read.

        The mission's compose file is the only authoritative list of the networks a run will
        create, and it ships inside the fused image rather than the installed bundle — so the
        confinement pass has to read it from there. A real driver either returns the content or
        raises; None is the stub's answer (no image store to read), never a real driver's way of
        reporting failure, because a silent empty answer would leave networks unconfined.

        Abstract, with no base implementation, precisely because of that last sentence: as a
        non-abstract `return None` default, a future real driver that simply never overrode it
        would silently disable confinement for every run it deployed, and nothing — not a log line,
        not a test — would say so. Declining is now something a driver has to write down.
        """

    @abstractmethod
    def inspect_by_name(self, name: str) -> ContainerHandle | None:
        """The live container's identity for this (run-id-derived) name, or None if absent.

        Keyed on the deterministic container name, not an in-memory handle, so the runner can
        report the true deploy state across a restart or from a second process —
        the live/durable fallback the reconcile-on-startup loop adopts."""

    @abstractmethod
    def image_exists(self, image: str) -> bool:
        """Whether the image is already in the local store (local-store-first pull)."""

    def image_labels(self, image: str) -> dict[str, str] | None:
        """The image's config labels (inherited included), or None if absent/unreadable.

        Non-abstract None default: only the real Docker driver inspects; other drivers degrade to
        None, which the base-compat gate reads as 'label unknown' and falls back to the tag."""
        return None

    def image_platform(self, image: str) -> str | None:
        """The `os/architecture` the local copy of the image was built for, or None.

        Non-abstract None default (same reasoning as image_labels): only the real driver
        inspects, and a None simply leaves the install record's platform unknown."""
        return None

    def daemon_platform(self) -> str | None:
        """The `os/architecture` the DAEMON executes natively (AS1), or None when unknown.

        This is deliberately the daemon's platform, not this Python process's: the daemon may
        live in a VM (Docker Desktop) or on another host, and it is where missions execute."""
        return None

    @abstractmethod
    def reap_managed(self) -> list[str]:
        """Force-remove every xorcise-managed per-run container; return what was reaped.

        Label-driven, so it reaps orphans this process never tracked — abandoned runs (the agent
        crashed before terminal) and leftovers from prior sessions, not just live deployments.
        """

    def container_state(self, name: str) -> ContainerState | None:
        """Liveness of the outer lifecycle container for this run-id name; None if absent/unknown.

        Lets the runner report a run whose environment DIED (container exited) instead of the flat
        READY that `inspect_by_name` alone produces — docker returns an exited container just the
        same, and ContainerHandle carries no state. Non-abstract None default: only the real Docker
        driver reports; other drivers degrade to the previous behaviour."""
        return None

    def compose_service_states(self, project: str) -> tuple[ServiceState, ...]:
        """The INNER mission-stack services for this run's compose project, or () if unknown.

        The mission + per-run router come up inside the outer container AFTER deploy returns, so
        this is how the server tells "still starting" from "up". Empty ⇒ unknown ⇒ the caller must
        degrade to ready rather than wedge the run at PENDING."""
        return ()

    def list_compose_projects(self) -> set[str]:
        """Every compose project still holding a network, so orphaned run environments (whose
        network outlived the run and keeps its subnet allocated) can be found. () when unknown."""
        return set()

    def remove_run_resources(self, run_id: str) -> None:
        """Release a run's containers/networks/volumes by project name. Idempotent; no-op by
        default (NOT abstract) so non-Docker drivers are unaffected."""
        return None

    def list_network_cidrs(self) -> set[str]:
        """Every subnet currently carved on the Docker host (all networks' IPAM subnets).

        The subnet allocator unions these into its in-use set so a LEFTOVER run network — one the
        run DB no longer tracks after an imperfect teardown — can never have its /24 re-handed to a
        new run. That reuse collides the new mission's compose network, so the mission container
        exits at deploy and the agent joins the tailnet with no reachable target. Non-abstract with
        an empty default: only the real Docker driver enumerates; stubs/tests opt in."""
        return set()


class StubDockerDriver(DockerDriver):
    def __init__(self) -> None:
        self.pulled: list[str] = []
        # last auth passed to pull(), for assertions
        self.pulled_auth: tuple[str, str] | None = None
        self.running: dict[str, ContainerHandle] = {}
        self.stopped: list[str] = []
        self.stopped_by_name: list[str] = []  # names passed to stop_by_name(), for assertions
        self.present: set[str] = set()
        self.network_cidrs: set[str] = set()  # test-settable in-use subnets for list_network_cidrs
        # Test seams for the readiness verdict: name → outer ContainerState, project → inner
        # ServiceStates. Unset ⇒ a deployed container reads as running and its inner stack as
        # unknown, so existing stub-mode behaviour (a plain READY) is preserved.
        self.container_states: dict[str, ContainerState] = {}
        self.service_states: dict[str, tuple[ServiceState, ...]] = {}
        self.compose_projects: set[str] = set()  # test-settable projects holding a network
        self.removed_projects: list[str] = []  # projects released by remove_run_resources
        self.specs: list[ContainerSpec] = []  # every run()'s spec, for assertions
        self.pulled_platform: str | None = None  # the last pull's explicit platform selection
        self._by_name: dict[str, str] = {}  # container name → container_id (by-name stop)
        self._counter = 0

    def pull(
        self,
        image: str,
        *,
        auth: tuple[str, str] | None = None,
        progress: Callable[[PullProgress], None] | None = None,
        platform: str | None = None,
    ) -> None:
        self.pulled.append(image)
        self.pulled_auth = auth
        self.pulled_platform = platform
        if progress is not None:
            # Two synthetic layer events (half, done) so stub-mode UI still animates.
            progress(PullProgress(layer_id="stub", status="Downloading", current=512, total=1024))
            progress(PullProgress(layer_id="stub", status="Downloading", current=1024, total=1024))
        self.present.add(image)  # after pull the image is local

    def image_exists(self, image: str) -> bool:
        return image in self.present

    def read_image_file(self, image: str, path: str) -> str | None:
        """No image store to read from — see `reads_image_files`. The stub creates no networks
        either, so there is nothing here left unconfined."""
        return None

    def run(self, spec: ContainerSpec) -> ContainerHandle:
        self._counter += 1
        self.specs.append(spec)
        handle = ContainerHandle(container_id=f"stub-{self._counter}", image=spec.image)
        self.running[handle.container_id] = handle
        self._by_name[spec.name] = handle.container_id
        return handle

    def stop(self, container_id: str) -> None:
        self.running.pop(container_id, None)
        self.stopped.append(container_id)

    def stop_by_name(self, name: str) -> bool:
        self.stopped_by_name.append(name)
        cid = self._by_name.pop(name, None)
        if cid is not None and cid in self.running:
            self.stop(cid)
            return True
        return False

    def inspect_by_name(self, name: str) -> ContainerHandle | None:
        cid = self._by_name.get(name)
        if cid is None:
            return None
        return self.running.get(cid)

    def reap_managed(self) -> list[str]:
        # Every container the driver runs is an xorcise-managed deploy, so reap = remove all
        # still-running ones (mirrors the real driver's label filter over the local store).
        reaped = list(self.running)
        for cid in reaped:
            self.stop(cid)
        return reaped

    def list_network_cidrs(self) -> set[str]:
        return set(self.network_cidrs)

    def container_state(self, name: str) -> ContainerState | None:
        override = self.container_states.get(name)
        if override is not None:
            return override  # a test-declared state wins even over the deploy cache
        cid = self._by_name.get(name)
        if cid is None or cid not in self.running:
            return None
        return ContainerState(status="running")

    def compose_service_states(self, project: str) -> tuple[ServiceState, ...]:
        return self.service_states.get(project, ())

    def list_compose_projects(self) -> set[str]:
        return set(self.compose_projects)

    def remove_run_resources(self, run_id: str) -> None:
        self.removed_projects.append(run_id)
        self.compose_projects.discard(run_id)
