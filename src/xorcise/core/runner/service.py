"""Runner-side control service — the plain entrypoint behind the server's ControlPort.

Runner part-island: it cannot import the ControlPort ABC (upward); the server-side
adapter (orchestration/clients/control.py) wraps this and implements the port. Speaks
only contracts DTOs. Brings the fused image up via the injected DockerDriver; the live
wait-ready + target discovery are integration concerns (deferred), so deploy here records
the run and returns its endpoints — unit-testable against the StubDockerDriver.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import yaml

from xorcise.core.contracts.control import (
    CollectTargetsResult,
    DeployRequest,
    NetworkSpec,
    RunId,
    RunnerEndpoints,
    RunState,
    StatusResult,
    TeardownResult,
)
from xorcise.core.contracts.errors import NotFoundError
from xorcise.core.runner.docker import ContainerHandle, ContainerSpec, DockerDriver
from xorcise.core.runner.netoverride import build_net_override, compose_network_names

# Where the entrypoint writes the delivered Headscale CA (in the OUTER fused container); the
# net-override bind-mounts this into the router. Kept in sync with the entrypoint.
_CA_PATH = "/mission/headscale-ca.pem"

# A run id (uuid4().hex) — the ONLY compose-project shape the orphan reaper may touch. Everything
# else on this daemon (notably the `xorcise-headscale` control plane) is off limits.
_RUN_ID_RE = re.compile(r"[0-9a-f]{32}")


# Where the fused image copies the mission bundle. The compose file's NAME within it comes from
# the manifest (`environment.compose_file`, default docker-compose.yml) and rides on MissionRef —
# build.py and preflight.py both honour it, so hardcoding a third copy of the default here would
# make any mission that authored `compose.yaml` unreadable, and therefore unconfined.
MISSION_BUNDLE_DIR = "/mission"


@dataclass
class _Deployed:
    endpoints: RunnerEndpoints
    handle: ContainerHandle


@dataclass
class RunnerControlService:
    driver: DockerDriver
    login_server: str = ""
    headscale_url: str = ""
    _deployed: dict[RunId, _Deployed] = field(default_factory=dict)
    _torn_down: set[RunId] = field(default_factory=set)

    def deploy(self, request: DeployRequest) -> RunnerEndpoints:
        existing = self._deployed.get(request.run_id)
        if existing is not None:
            return existing.endpoints  # idempotent replay — no double-apply
        if not request.network.auth_key:
            # fail loud rather than deliver an empty TS_AUTHKEY downstream — the
            # router's `tailscale up` would abort under `set -eu` with a cryptic error. An empty
            # key means the fence never minted the router key.
            raise ValueError(
                f"deploy({request.run_id}): empty tailnet auth key — the router key was not "
                "minted; refusing to deploy a router that cannot join the tailnet"
            )
        handle = self.driver.run(
            ContainerSpec(
                image=request.mission.image,
                name=request.run_id,
                env=self._deploy_env(
                    request.run_id,
                    request.network,
                    image=request.mission.image,
                    compose_file=request.mission.compose_file,
                ),
            )
        )
        endpoints = RunnerEndpoints(
            run_id=request.run_id,
            # NOTE: this is NOT the agent-facing run-control URL. The prompt's run-control
            # base is computed in rest/run_create._run_control_url (the server's REST API on the
            # agent-facing host); this runner-side echo stays only for the RunnerEndpoints contract.
            control_url=f"{self.headscale_url}/runs/{request.run_id}",
            otlp_url=f"{self.headscale_url}/otlp/{request.run_id}",
        )
        self._deployed[request.run_id] = _Deployed(endpoints=endpoints, handle=handle)
        self._torn_down.discard(request.run_id)
        return endpoints

    def _deploy_env(
        self,
        run_id: RunId,
        network: NetworkSpec,
        *,
        image: str = "",
        compose_file: str = "docker-compose.yml",
    ) -> tuple[tuple[str, str], ...]:
        """The env the fused entrypoint needs: the per-run net-override (the mission nets +
        the Tailscale router as an inner container) plus the secrets compose interpolates into
        it. The override is base64'd so multi-line YAML rides a single env var cleanly; the
        auth key travels as its own env var and is never written into the override file.

        Air-gapped: when a TLS CA is supplied it rides as XORCISE_HEADSCALE_CA_B64 (the
        entrypoint writes it to _CA_PATH) and the override mounts it into the router + sets
        SSL_CERT_FILE; extra_hosts let the router resolve the Headscale TLS hostname."""
        assert network.auth_key, "auth_key is guaranteed non-empty by deploy()"
        # rebuild the per-network subnet map from the contract the fence already
        # handed us (entry_networks + the routes it carved + advertised) — do NOT re-carve here.
        # Re-carving was a silent-desync trap: a future change to either carve site could pin
        # compose subnets that no longer match the advertised routes. strict=True fails loud if
        # the two ever arrive mismatched.
        entry_subnets = dict(zip(network.entry_networks, network.routes, strict=True))
        routes = network.routes
        ca_path = _CA_PATH if network.ca_cert else ""
        # Every network the mission's compose will create, read from the image itself — the
        # only authoritative source (the installed bundle carries the manifest, not the compose).
        # Confining just the carved entry networks would leave a multi-homed service its route off
        # box, so this is what makes the tailnet the ONLY path to the agent.
        compose_text = (
            self.driver.read_image_file(image, f"{MISSION_BUNDLE_DIR}/{compose_file}")
            if image
            else None
        )
        if not compose_text and self.driver.reads_image_files and not network.allow_egress:
            # Fail CLOSED. A driver that has an image store and still yields nothing has failed to
            # read, and the only thing an empty answer can do downstream is silently narrow
            # confinement to the carved entry networks — leaving a multi-homed service its route
            # off box, which is the whole hole this exists to close. A run that cannot be confined
            # must not deploy pretending it was. Drivers with no image store (the stub) create no
            # networks, and an `allow_egress` mission is not being confined in the first place.
            raise ValueError(
                f"deploy({run_id}): could not read {compose_file!r} from image {image!r} — "
                "refusing to deploy, because the mission's networks cannot be confined without it"
            )
        all_networks = compose_network_names(compose_text) if compose_text else ()
        override = build_net_override(
            run_id,
            entry_subnets,
            extra_hosts=network.extra_hosts,
            ca_cert_path=ca_path,
            static_ips=network.static_ips,
            all_networks=all_networks,
            agent_user=network.agent_user,
            allow_egress=network.allow_egress,
        )
        override_b64 = base64.b64encode(yaml.safe_dump(override).encode()).decode()
        env = [
            ("XORCISE_PROJECT", run_id),
            ("XORCISE_LOGIN_SERVER", network.login_server or self.login_server),
            ("XORCISE_AUTHKEY", network.auth_key),
            ("XORCISE_ROUTES", ",".join(routes)),
            ("XORCISE_NET_OVERRIDE_B64", override_b64),
        ]
        if network.ca_cert:
            env.append(
                ("XORCISE_HEADSCALE_CA_B64", base64.b64encode(network.ca_cert.encode()).decode())
            )
        return tuple(env)

    def status(self, run_id: RunId) -> StatusResult:
        # report the TRUE deploy state, not just this process's cache. _deployed
        # is a cache; its miss falls back to the live container (named run_id) so status survives a
        # server restart / a second process — the state the reconcile loop adopts. Only a genuinely
        # absent container is TORN_DOWN (torn down here) or NotFound (never deployed/unrecoverable).
        if run_id not in self._deployed and self.driver.inspect_by_name(run_id) is None:
            if run_id in self._torn_down:
                return StatusResult(run_id=run_id, state=RunState.TORN_DOWN)
            raise NotFoundError(run_id)
        # Present — but presence is NOT readiness: deploy() only STARTS the outer container, and the
        # mission stack + per-run router come up inside it afterwards. Docker also hands back an
        # EXITED container just like a running one, so read the real state instead of assuming it.
        state = self.driver.container_state(run_id)
        if state is not None and state.exited:
            # The environment died at/after deploy (a failed `compose up` kills the `set -eu`
            # entrypoint). Read from the LIVE container, so this process's deploy cache cannot mask
            # it — the server closes the run out instead of leaving an agent on a dead target.
            return StatusResult(run_id=run_id, state=RunState.FAILED, ready=False)
        services = self.driver.compose_service_states(run_id)
        if services and not all(s.running for s in services):
            return StatusResult(run_id=run_id, state=RunState.PENDING, ready=False)
        # Every inner service is up — or the driver cannot report them (stub / non-Docker), which
        # degrades to READY so an unreportable environment is never wedged at PENDING forever.
        return StatusResult(run_id=run_id, state=RunState.READY, ready=True)

    def collect_targets(self, run_id: RunId) -> CollectTargetsResult:
        # Cache miss falls back to the live container by name, as status() does.
        if run_id not in self._deployed and self.driver.inspect_by_name(run_id) is None:
            raise NotFoundError(run_id)
        return CollectTargetsResult(run_id=run_id)

    def reap_orphan_environments(self, keep: Sequence[RunId]) -> list[RunId]:
        """Release run environments whose compose project still holds a network but whose run is
        gone. Returns what was reaped.

        A leaked network keeps its /24 allocated, so without this the run pool bleeds a subnet per
        imperfect teardown. SAFETY: only projects whose name is a run id (a 32-char hex uuid4) are
        even considered — the Headscale control plane and any operator stack share this daemon, and
        removing their network would take the control plane down for every run. Anything the caller
        still considers live is kept."""
        keep_set = set(keep)
        reaped: list[RunId] = []
        for project in sorted(self.driver.list_compose_projects()):
            if not _RUN_ID_RE.fullmatch(project) or project in keep_set:
                continue
            self.driver.remove_run_resources(project)
            reaped.append(project)
        return reaped

    def teardown(self, run_id: RunId) -> TeardownResult:
        # stop the container by its deterministic run-id name, NOT the in-memory
        # handle — so teardown works after a server restart or from a second process (stateless,
        # keyed on run_id). The _deployed pop just clears this process's cache if present.
        self._deployed.pop(run_id, None)
        self.driver.stop_by_name(run_id)
        self._torn_down.add(run_id)  # idempotent — repeat teardown stays ok
        return TeardownResult(run_id=run_id, ok=True)
