"""Config + system-info wire DTOs (LEAF). Imports only sibling contracts.

The config surface is FIXED-SHAPE: the BYOM judge trio and the terrain-attribution
model override are writable, and the secret key is write-only — `ConfigView` carries `configured`
+ a masked `key_hint`, never the raw key. `SystemInfo` is the read-only Reflect view (role /
planes / db schema / catalog / remotes)
behind the GUI System card; it mirrors what `status`/`doctor`/`role`/`catalog status`/`remote list`
show on the CLI."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JudgeConfigView(_Frozen):
    """The BYOM judge model's status. `key_hint` is a masked tail (e.g. `…abcd`), never the key."""

    configured: bool
    base_url: str | None = None
    model_name: str | None = None
    key_hint: str | None = None
    timeout_seconds: float | None = None
    transcript_max_tokens: int | None = None
    # Per-span body cap (tokens) applied to the distilled transcript before grading; 0 = disabled.
    span_max_tokens: int | None = None
    tokenizer: str | None = None


class JudgeTestResult(_Frozen):
    """Result of a live judge-key check (POST /config/model/test).

    `configured` is only a presence check, so this actually calls the model. `ok` = the model
    answered; `status` distinguishes a missing config from a real error; `message` carries the
    provider error on failure."""

    ok: bool
    status: Literal["ok", "not_configured", "error"]
    message: str | None = None
    model_name: str | None = None


class TerrainModelConfigView(_Frozen):
    """The terrain-attribution model's effective status. Defaults to the judge config;
    `uses_judge_default` is False once any terrain-specific override is set. Write-only key —
    `key_hint` is a masked tail, never the key."""

    configured: bool
    uses_judge_default: bool
    base_url: str | None = None
    model_name: str | None = None
    key_hint: str | None = None
    transcript_max_tokens: int | None = None


class CatalogConfigView(_Frozen):
    """The XORCISE remote catalog's connection state for the Settings switch.

    `connected` = the switch is on AND an endpoint is configured; `url` is the endpoint
    the local app points at (shown read-only — the operator only toggles connect/disconnect)."""

    connected: bool
    url: str | None = None


class NetworkConfigView(_Frozen):
    """Distributed-mode network addresses the local server dials (editable, applies on restart).

    Both default empty in single-host local mode; set them to point at a remote Headscale
    control plane / advertise a reachable host when running modules across machines."""

    headscale_url: str | None = None
    advertise_host: str | None = None


class ConfigView(_Frozen):
    judge: JudgeConfigView
    terrain: TerrainModelConfigView
    default_budget_seconds: int
    catalog: CatalogConfigView
    network: NetworkConfigView


class ModelConfigUpdate(_Frozen):
    """A partial update to the judge trio. Empty/None field = leave/remove that env var."""

    key: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    # None = leave unchanged; 0 = disable the pre-flight cap (attempt & rely on the model's limit).
    transcript_max_tokens: int | None = Field(default=None, ge=0)
    # Per-span body cap (tokens); None = leave unchanged, 0 = disable the cap (ge=0, not gt=0).
    span_max_tokens: int | None = Field(default=None, ge=0)
    tokenizer: str | None = None


class TerrainModelConfigUpdate(_Frozen):
    """Partial update to the terrain-model override.

    None = leave; "" = unset (fall back to judge). `transcript_max_tokens` is the per-call prompt
    token safety cap (None = leave)."""

    key: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    transcript_max_tokens: int | None = Field(default=None, gt=0)


class CatalogConfigUpdate(_Frozen):
    """Flip the remote-catalog connect/disconnect switch (persists catalog_enabled)."""

    connected: bool


class NetworkConfigUpdate(_Frozen):
    """Partial update to the distributed network addresses. None = leave; "" = unset to default."""

    headscale_url: str | None = None
    advertise_host: str | None = None


class PlaneStatus(_Frozen):
    """One module's reachability, tagged with the service role that owns it.

    `role` + `label` exist so the operator surfaces can group modules the way XORCISE is
    actually built (control / runner / headscale / collector) instead of listing bare
    plane keys. They are the SAME role keys `xorcise serve --role <key>` takes, which is
    what makes a future multi-host deployment legible: when a module moves to another
    box only `location` changes. `name` stays the raw key — the CLI, the JSON contract
    and the existing tests key off it.
    """

    name: str
    ok: bool
    detail: str
    location: str = ""  # where the module runs, e.g. "<host>:<port>" or "local daemon"
    role: str = ""  # owning service role: control | runner | headscale | collector
    label: str = ""  # human name, e.g. "REST API" (mirrors the CLI service vocabulary)
    # `ok` is a two-state answer, and "this host does not run that module" is not a failure.
    # A control-only server serves no OTLP receiver: reporting that as down would paint a
    # correctly-configured host red. `state` separates the two; `ok` stays exactly
    # `state == "ok"` so every existing reader keeps working.
    state: Literal["ok", "down", "not_deployed"] = "ok"


class CatalogStatusView(_Frozen):
    """Self-contained mirror of catalog reachability (contracts are leaves — no cross-import)."""

    state: Literal["connected", "error", "disconnected"]
    message: str | None = None
    last_sync: str | None = None


class MissionBaseView(_Frozen):
    """The mission-base picture for settings/diagnostics (contract §27/§36): what THIS client
    requires (the compatibility MAJOR it was built for) beside what the catalog currently
    promotes. The promoted side is None when the catalog predates the endpoint (prod today)
    or is unreachable — unknown, never fabricated."""

    required_major: int
    client_version: str = ""  # this XORCISE client's own package version
    promoted_version: str | None = None  # e.g. "2.0.0" — the promoted base SemVer
    promoted_index_digest: str | None = None


class SystemInfo(_Frozen):
    """Read-only Reflect view powering the GUI System / Modules / Catalog cards."""

    role: str
    planes: tuple[PlaneStatus, ...]
    db_schema: Literal["head", "behind", "fresh", "unknown"]
    catalog: CatalogStatusView
    remotes: tuple[str, ...] = ()
    home: str = ""  # resolved XORCISE_HOME (the install path)
    db_url: str = ""  # the active database url (sqlite path under home by default)
    topology: Literal["local", "distributed"] = "local"
    mission_base: MissionBaseView | None = None  # §36 version visibility (None: very old server)
