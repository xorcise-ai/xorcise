"""Server↔runner control-contract DTOs (LEAF).

Wire DTOs only — the ControlPort ABC lives in orchestration/ports.py (beside its
application-layer owner), never here. Transport-agnostic: the same shapes ride Shape A (HTTP)
and Shape B (queue/pull). Imports nothing internal (stdlib + pydantic only).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

RunId = str
ApiKey = str


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MissionRef(_Frozen):
    """Thin reference to a mission: a metadata id + a pull-able OCI image.

    The mission-delivery model is mission.json metadata + a container image the
    runner pulls and spins up (NOT a mission.zip). Full metadata schema is owned
    by the mission-ingestion story; only the shape direction is locked here.
    """

    mission_id: str
    image: str  # OCI image ref, e.g. ghcr.io/xorcise/mission-xyz:1.2.3


class InstalledImageIdentity(_Frozen):
    """The mission-image identity of an install (mission-versioning contract §30).

    `release_ref` (immutable `<mission-version>-base<base-version>` tag) and the digests are
    identity; `pull_ref` (the moving `:latest` pointer) is delivery convenience and MUST never
    be treated as identity. `platform`/`platform_digest` record what was ACTUALLY pulled for
    this machine, not what the catalog offers. All optional: a pre-contract catalog serves
    none of it."""

    pull_ref: str | None = None
    release_ref: str | None = None
    index_digest: str | None = None  # OCI index digest — the strongest update-check signal
    platform: str | None = None  # e.g. "linux/amd64" — the platform this install pulled
    platform_digest: str | None = None  # per-platform manifest digest of what executes


class InstalledBaseIdentity(_Frozen):
    """The mission-base THIS artifact was fused onto (§30) — not the currently promoted base;
    the two diverge the moment a newer base is promoted without a fleet backfill."""

    version: str | None = None  # base SemVer, e.g. "2.0.0"
    index_digest: str | None = None  # GHCR base index digest
    platform_digest: str | None = None  # base child digest for the pulled platform


class MissionInstallIdentity(_Frozen):
    """The §30 slice of installed.json: what exactly this machine installed.

    Splatted to the record's top level by InstalledMission.to_record so the on-disk shape
    matches the contract's recommended layout. Written at pull time from the catalog detail
    response + a post-pull image inspect; never re-resolved later (evidence copies it at run
    create and must not depend on the future value of any floating tag)."""

    mission_version: str | None = None  # creator SemVer projected by the catalog
    mission_base_version: str | None = None
    content_hash: str | None = None  # bare hex — identical to ai.xorcise.content.hash
    image: InstalledImageIdentity | None = None
    mission_base: InstalledBaseIdentity | None = None
    pulled_at: str | None = None  # ISO-8601 UTC of the pull


class NetworkSpec(_Frozen):
    """Per-run network spec the runner needs to fence the run.

    The server mints these from the fence + the mission manifest and the runner turns them
    into the per-run compose net-override (entry subnets + Tailscale router). `auth_key` is the
    one-time Headscale pre-auth key; it is delivered to the container as an env var and never
    written into the override file (the router service references it as ${XORCISE_AUTHKEY}).
    """

    tailnet: str | None = None  # the run's CIDR (carved across entry_networks)
    auth_key: str | None = None  # one-time Headscale pre-auth key for this run's router
    login_server: str | None = None  # Headscale URL the router logs in to
    entry_networks: tuple[str, ...] = ()  # compose network names the agent enters through
    routes: tuple[str, ...] = ()  # CIDRs the router advertises (the carved entry subnets)
    ca_cert: str | None = None  # PEM of the Headscale TLS CA the router must trust (air-gapped)
    extra_hosts: tuple[str, ...] = ()  # router /etc/hosts entries, e.g. "headscale.local:10.0.0.1"
    # mission service->network->last-octet pins, from the manifest's environment.
    # static_ips; the runner turns these into per-service ipv4_address entries in the net-override
    # so a service comes up at its authored address instead of a docker-sequential one.
    static_ips: dict[str, dict[str, int]] = Field(default_factory=dict)


class DeployRequest(_Frozen):
    run_id: RunId
    mission: MissionRef
    network: NetworkSpec = Field(default_factory=NetworkSpec)


class RunnerEndpoints(_Frozen):
    run_id: RunId
    control_url: str
    otlp_url: str


class RunState(StrEnum):
    PENDING = "pending"  # deployed; the inner mission stack is still coming up
    READY = "ready"
    TORN_DOWN = "torn_down"
    # The environment died at/after deploy — the outer lifecycle container exited before teardown
    # (its entrypoint runs `set -eu`, so e.g. a failed `compose up` kills it). Distinct from
    # TORN_DOWN (deliberate) and from an absent container (NotFound): the run cannot proceed and
    # must be closed out instead of leaving an agent working against an environment that is gone.
    FAILED = "failed"


class Target(_Frozen):
    host: str
    port: int


class StatusResult(_Frozen):
    run_id: RunId
    state: RunState
    targets: tuple[Target, ...] = ()
    ready: bool = False


class CollectTargetsResult(_Frozen):
    run_id: RunId
    targets: tuple[Target, ...] = ()


class TeardownResult(_Frozen):
    run_id: RunId
    ok: bool = True
