"""xorcise.core.config — settings, env, host/port constants.

LAYER: SHARED-KERNEL. Imports only stdlib + pydantic-settings. Module-level
constants are side-effect-free; Settings load only when get_settings() is called.
Does NOT import xorcise.core.home (a domain module) — the home path is re-derived
locally so the kernel never imports upward (import-linter `layers` contract).
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict, TomlConfigSettingsSource
from pydantic_settings.sources import PydanticBaseSettingsSource

LOOPBACK = "127.0.0.1"
# Spellings under which the host loopback is reachable — for bind-equivalence checks
# (double-binding an alias of an already-bound loopback would EADDRINUSE at boot).
LOOPBACK_HOSTS = (LOOPBACK, "localhost", "::1")
HOST = LOOPBACK
REST_PORT = 3001
OTLP_PORT = 4318
RUNNER_PORT = 8800
HEADSCALE_PORT = 8080

# The XORCISE.AI remote mission catalog the local app points at by default — the
# production tier. The local app just needs the endpoint configured; the operator
# connects/disconnects it via the GUI switch (catalog_enabled). Point a dev build at
# another tier by setting XORCISE_CATALOG_URL.
REMOTE_CATALOG_URL = "https://api.xorcise.ai"


def ui_url() -> str:
    """The URL the operator UI is served at (host + REST port from settings)."""
    s = get_settings()
    return f"http://{s.host}:{s.rest_port}/ui"


def _home() -> Path:
    """Resolve XORCISE_HOME -> ~/.xorcise. Re-derived here (not imported from
    xorcise.core.home): config is SHARED-KERNEL and must not import a domain module."""
    override = os.environ.get("XORCISE_HOME")
    return Path(override) if override else Path.home() / ".xorcise"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XORCISE_", extra="ignore", protected_namespaces=()
    )

    # BYOM — the user's OpenAI-compatible model (secret -> .env). XORCISE bundles none.
    model_key: str | None = None
    model_base_url: str | None = None
    model_name: str | None = None
    # BYOM judge HTTP timeout (seconds). Default raised from a hardcoded 60s so slow/reasoning
    # models don't degrade to judge-unavailable. Set via `config set-model --timeout`.
    model_timeout_seconds: float = 120.0
    # Terrain attribution model. OPTIONAL per-field override of the judge trio: each
    # effective field falls back to the judge's model_* when unset, so by default terrain uses the
    # same BYOM model as the judge. Timeout reuses model_timeout_seconds.
    terrain_model_key: str | None = None
    terrain_model_base_url: str | None = None
    terrain_model_name: str | None = None
    # Token ceiling on ONE terrain-attribution model call (system + evidence prompt). Unlike the
    # judge (which feeds the whole distilled transcript), terrain attributes in bounded per-event
    # BATCHES, so this is a SAFETY CAP: when a batch's prompt would exceed it, the batch is shrunk
    # (trailing events dropped) until it fits — attribution still makes progress, just fewer events
    # per call. The default is high enough to never bite a normal batch; lower it for a
    # small-context (e.g. 32K) attribution model. Counted with judge_tokenizer.
    terrain_transcript_max_tokens: int = 256000
    # Attribute the terrain mission plane automatically when a run goes terminal (in
    # grade_and_record), independent of any viewer, so every run's map is complete post-hoc. Default
    # on; still a no-op without a configured terrain model. False keeps BYOM spend strictly viewed.
    terrain_auto_attribute: bool = True
    # OPTIONAL pre-flight ceiling (TOKENS) on the estimated judge prompt. DEFAULT 0 = DISABLED: the
    # judge attempts the call and lets the model's OWN context limit be the gate — the provider
    # returns an accurate error (surfaced on the result) and a failed judge is re-gradeable. The
    # tokenizer below is only an ESTIMATE of the real model tokenizer (a documented approximation
    # for non-OpenAI BYOM models), so a hard estimate-gate risks false rejections; set a positive
    # value only to pre-reject oversized evidence on a small-context self-hosted model. Mirror of
    # eval.judge.DEFAULT_JUDGE_TRANSCRIPT_MAX_TOKENS (config is SHARED-KERNEL, cannot import eval).
    judge_transcript_max_tokens: int = 0
    # Per-span body cap (TOKENS) applied to the distilled transcript before it is fed to the judge
    # (Lever 1). Transcripts are fat-tailed — a few giant tool-output spans dominate the token
    # count — so bounding each span to a head+tail window with an elision marker collapses that tail
    # while keeping every span (no rubric criterion loses an action). 0 disables. Counted with
    # judge_tokenizer. Mirror of eval.judge.DEFAULT_JUDGE_SPAN_MAX_TOKENS (config is SHARED-KERNEL,
    # cannot import eval).
    judge_span_max_tokens: int = 2000
    # The tokenizer used to count the transcript budget. Tokenizers are NOT interchangeable — a
    # count under one BPE differs from another — so the choice is explicit and recorded here. A
    # tiktoken encoding name (e.g. "o200k_base" for GPT-4o/o-series, "cl100k_base" for GPT-4/3.5);
    # an unknown name degrades to a coarse chars/4 heuristic (build_token_counter logs a warning).
    # For a non-OpenAI BYOM judge (e.g. a vLLM-served Qwen) this is a documented APPROXIMATION.
    judge_tokenizer: str = "o200k_base"
    # persistence — empty default is filled with a sqlite path under the home in get_settings()
    database_url: str = ""
    # mission install store — empty default filled with <home>/missions in get_settings()
    missions_root: str = ""
    # tailscale client cache — empty default filled with <home>/cache/tailscale in get_settings().
    # The server caches the pinned static client here so run-control can serve it to an air-gapped
    # agent (which then needs no public egress to install tailscale for the tailnet join).
    tailscale_cache_root: str = ""
    # mission catalog — the remote endpoint and the operator's connect/disconnect switch.
    # Connected = enabled AND a url is set; the GUI toggles catalog_enabled (never clears the
    # url, so disconnect is a clean boolean, not an empty-string-that-reverts-to-default — see
    # set_env_vars drops empties). Defaults to the production catalog above; clear it and the
    # library reports `disconnected` rather than erroring against an unreachable host.
    catalog_url: str | None = REMOTE_CATALOG_URL
    catalog_enabled: bool = True
    catalog_key: str | None = None
    # force stub adapters (Docker-less dev/CI); default real — real-vs-stub also keys off `role`
    use_stubs: bool = False
    # platform passed to docker pull/run. Mission images are built amd64-only, so an
    # arm64 host (Apple Silicon) must request linux/amd64 or the pull 404s on a missing arm64
    # manifest; Docker Desktop then runs it under emulation. Empty ⇒ docker picks the host platform.
    docker_platform: str = "linux/amd64"
    # role + service endpoints (defaults mirror the module constants)
    role: str = "all"
    host: str = HOST
    rest_port: int = REST_PORT
    otlp_port: int = OTLP_PORT
    runner_port: int = RUNNER_PORT
    headscale_port: int = HEADSCALE_PORT
    # per-run network fence (Headscale)
    headscale_container: str = "headscale"
    orchestrator_user: str = "orchestrator"
    router_tag: str = "tag:router"
    # air-gapped TLS + embedded-DERP Headscale. Defaults keep the plain-HTTP dev path:
    # when headscale_url is set it overrides the derived http://{host}:{headscale_port} login
    # server; ca_cert (a PEM path) is delivered to each run's router so it trusts the self-signed
    # control cert; host_alias ("headscale.local:<ip>") is injected as the router's extra_hosts so
    # it resolves the TLS hostname to the host from inside the fused container's nested network.
    headscale_url: str = ""
    headscale_ca_cert: str = ""
    headscale_host_alias: str = ""
    # The host the per-run router/agents dial for the control plane. `host` (127.0.0.1) is the
    # REST bind and is NOT reachable from inside the fused container's nested network, so a
    # real run derives its plain-HTTP login server from this instead (the docker-bridge gateway;
    # written by `xorcise up`). Empty => fall back to `host` (fine for stub/local).
    headscale_advertise_host: str = ""
    # controller/collector reachability topology
    deployment_topology: Literal["local", "distributed"] = "local"
    base_network: str = "10.200.0.0/16"
    cidr_prefix: int = 24
    key_expiration: str = "1h"
    # run budget
    default_budget_seconds: int = 3600
    # reserved, default-OFF seam to mirror traces to a customer OTel service.
    # The product ships local-only telemetry — nothing leaves the host by default; the
    # mirror is a natural future otelcol second-exporter. Enabling it
    # fails fast (otel.mirror.resolve_mirror) — no exporter is built.
    otel_mirror_enabled: bool = False
    otel_mirror_endpoint: str = ""
    # HMAC secret for short-lived attachment download URLs + their TTL.
    # Empty default: get_settings() fills it with a fresh random secret per process rather than
    # shipping a known key that would let anyone forge an attachment download URL. Set
    # XORCISE_RUN_CONTROL_SIGNING_SECRET explicitly to pin it across restarts or share it
    # between processes; otherwise links minted before a restart stop verifying after one
    # (harmless at the 300s TTL below).
    run_control_signing_secret: str = ""
    attachment_ttl_seconds: int = 300
    # budget watchdog scan interval (seconds). Terminates expired runs with no traffic.
    budget_watchdog_interval_seconds: float = 5.0
    # How long a deployed run may take to bring its environment up (mission stack + subnet
    # router) before it is closed out as `deploy_failed`. deploy() does not block on that bring-up,
    # so without this a run whose environment died sat non-terminal forever with a live agent
    # working a target that never existed. Generous by default: a cold mission stack is slow, and
    # an over-eager gate would kill legitimately slow runs. 0 disables the gate.
    readiness_timeout_seconds: float = Field(default=90.0, ge=0.0)
    readiness_scan_interval_seconds: float = 5.0
    # Keep OTLP ingestion open briefly after the run-control plane becomes terminal. Short-lived
    # harnesses commonly flush their final tool result only after /complete returns; sealing before
    # that flush permanently rejects the last span/log batch.
    telemetry_drain_seconds: float = Field(default=5.0, ge=0.0, le=300.0)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # priority high -> low: init > process env > .env > config.toml > secrets (env wins)
        toml = TomlConfigSettingsSource(settings_cls, toml_file=_home() / "config.toml")
        return (init_settings, env_settings, dotenv_settings, toml, file_secret_settings)

    def model_configured(self) -> bool:
        """True when a BYOM key is set (the judge half degrades cleanly when False)."""
        return bool(self.model_key)

    def terrain_model_effective(self) -> tuple[str | None, str | None, str | None, float]:
        """(key, base_url, name, timeout) for terrain attribution — each field falls back to the
        judge config; timeout reuses the judge timeout."""
        return (
            self.terrain_model_key or self.model_key,
            self.terrain_model_base_url or self.model_base_url,
            self.terrain_model_name or self.model_name,
            self.model_timeout_seconds,
        )

    def terrain_model_configured(self) -> bool:
        return bool(self.terrain_model_effective()[0])

    def terrain_model_overridden(self) -> bool:
        return any((self.terrain_model_key, self.terrain_model_base_url, self.terrain_model_name))


@lru_cache
def get_settings() -> Settings:
    home = _home()
    settings = Settings(_env_file=home / ".env")  # type: ignore[call-arg]
    if not settings.database_url:
        settings.database_url = f"sqlite:///{home / 'xorcise.db'}"
    if not settings.missions_root:
        settings.missions_root = str(home / "missions")
    if not settings.tailscale_cache_root:
        settings.tailscale_cache_root = str(home / "cache" / "tailscale")
    if not settings.run_control_signing_secret:
        settings.run_control_signing_secret = secrets.token_urlsafe(32)
    return settings
