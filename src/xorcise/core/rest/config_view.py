"""Config browse + write coordinator (delivery/rest layer).

Sits in rest (not the kernel) because it reads `config` (SHARED-KERNEL) and writes via the `home`
module — a delivery surface may sit above both (.importlinter layers; same pattern as
catalog_view).

Fixed-shape + write-only key: `build_config_view` masks the BYOM key to a tail hint and
never returns it; `apply_model_update` upserts the judge trio into ~/.xorcise/.env and clears the
settings cache so the judge picks up the new model with no restart — `apply_terrain_model_update`
does the same for the terrain-attribution override.
"""

from __future__ import annotations

from pathlib import Path

from xorcise.core.config import Settings, get_settings
from xorcise.core.contracts.config import (
    CatalogConfigView,
    ConfigView,
    JudgeConfigView,
    JudgeTestResult,
    ModelConfigUpdate,
    NetworkConfigUpdate,
    NetworkConfigView,
    TerrainModelConfigUpdate,
    TerrainModelConfigView,
)
from xorcise.core.eval.judge import JudgeError
from xorcise.core.home import set_env_vars
from xorcise.core.orchestration.clients.judge_model import build_judge_model, build_terrain_model


def _mask(key: str | None) -> str | None:
    """Masked tail for display — never the raw key. `…abcd` for long keys, `set` for short ones."""
    if not key:
        return None
    return f"…{key[-4:]}" if len(key) >= 4 else "set"


def build_config_view(settings: Settings) -> ConfigView:
    return ConfigView(
        judge=JudgeConfigView(
            configured=settings.model_configured(),
            base_url=settings.model_base_url,
            model_name=settings.model_name,
            key_hint=_mask(settings.model_key),
            timeout_seconds=settings.model_timeout_seconds,
            transcript_max_tokens=settings.judge_transcript_max_tokens,
            span_max_tokens=settings.judge_span_max_tokens,
            tokenizer=settings.judge_tokenizer,
        ),
        terrain=TerrainModelConfigView(
            configured=settings.terrain_model_configured(),
            uses_judge_default=not settings.terrain_model_overridden(),
            base_url=settings.terrain_model_effective()[1],
            model_name=settings.terrain_model_effective()[2],
            key_hint=_mask(settings.terrain_model_key or settings.model_key),
            transcript_max_tokens=settings.terrain_transcript_max_tokens,
        ),
        default_budget_seconds=settings.default_budget_seconds,
        catalog=CatalogConfigView(
            connected=settings.catalog_enabled and bool(settings.catalog_url),
            url=settings.catalog_url,
        ),
        network=NetworkConfigView(
            headscale_url=settings.headscale_url or None,
            advertise_host=settings.headscale_advertise_host or None,
        ),
    )


def explain_model_failure(detail: str) -> str:
    """Turn a raw provider/transport error into a sentence an operator can act on.

    The underlying text is httpx's ("Client error '401 Unauthorized' for url … For more
    information check: <mdn link>"), which tells a developer what happened but not what to
    do. Lead with the action and keep the original detail after it, since a base-URL typo
    and an expired key both surface as 4xx.
    """
    lowered = detail.lower()
    lead = None
    if "401" in detail or "unauthorized" in lowered:
        lead = (
            "The provider rejected this API key. Check the key is current and matches the base URL."
        )
    elif "403" in detail or "forbidden" in lowered:
        lead = "The provider refused this key. It may lack access to this model."
    elif "404" in detail or "not found" in lowered:
        lead = "The provider has no such endpoint or model. Check the model name and base URL."
    elif "429" in detail or "rate limit" in lowered:
        lead = "The provider is rate-limiting this key. Try again shortly."
    elif "timeout" in lowered or "timed out" in lowered:
        lead = "The model did not answer in time. Check the base URL, or raise the timeout."
    elif "connect" in lowered or "resolve" in lowered or "refused" in lowered:
        lead = "Could not reach the provider. Check the base URL and your network."
    return f"{lead} ({detail})" if lead else detail


def run_judge_live_test(settings: Settings) -> JudgeTestResult:
    """Actually call the judge model with the saved config to prove the key works.

    Reuses the exact grade-time client (build_judge_model + OpenAiCompatibleJudgeModel.score) so
    the check exercises the real code path. Returns a not_configured result when no key is set
    (no network), ok when the model answers, and error with the provider message otherwise."""
    model = build_judge_model(settings)
    if model is None:
        return JudgeTestResult(
            ok=False,
            status="not_configured",
            message="No judge API key configured.",
        )
    try:
        model.score([("system", "You are a connectivity check."), ("user", "Reply with: ok")])
    except JudgeError as exc:
        return JudgeTestResult(
            ok=False,
            status="error",
            message=explain_model_failure(str(exc)),
            model_name=settings.model_name,
        )
    return JudgeTestResult(ok=True, status="ok", model_name=settings.model_name)


def run_terrain_live_test(settings: Settings) -> JudgeTestResult:
    """Actually call the terrain attribution model with the effective config to prove it works.

    Reuses the exact attribution-time client (build_terrain_model), which resolves the per-field
    terrain override falling back to the judge trio — so this exercises the real code path,
    including a custom (advanced-mode) model. not_configured when no key resolves, ok when the
    model answers, error with the provider message otherwise. The reported name is the terrain
    EFFECTIVE model, which may differ from the judge's when overridden."""
    effective_name = settings.terrain_model_effective()[2]
    model = build_terrain_model(settings)
    if model is None:
        return JudgeTestResult(
            ok=False,
            status="not_configured",
            message="No terrain attribution model configured.",
        )
    try:
        model.score([("system", "You are a connectivity check."), ("user", "Reply with: ok")])
    except JudgeError as exc:
        return JudgeTestResult(
            ok=False,
            status="error",
            message=explain_model_failure(str(exc)),
            model_name=effective_name,
        )
    return JudgeTestResult(ok=True, status="ok", model_name=effective_name)


def apply_network_update(home: Path, update: NetworkConfigUpdate) -> ConfigView:
    """Persist the distributed network addresses to `<home>/.env`, refresh, return the view.

    A field left None is untouched; "" removes that env var (reverts to the empty default).
    These apply at the next server start — the GUI surfaces a "restart to apply" note.
    """
    env_updates: dict[str, str | None] = {}
    if update.headscale_url is not None:
        env_updates["XORCISE_HEADSCALE_URL"] = update.headscale_url
    if update.advertise_host is not None:
        env_updates["XORCISE_HEADSCALE_ADVERTISE_HOST"] = update.advertise_host
    set_env_vars(home, env_updates)
    get_settings.cache_clear()
    return build_config_view(get_settings())


def apply_catalog_update(home: Path, connected: bool) -> ConfigView:
    """Flip the remote-catalog switch: persist catalog_enabled to `<home>/.env`, refresh, return.

    Writes a boolean (never an empty value) so disconnect persists cleanly — clearing the url
    would be dropped by the .env upsert and silently revert to the connected default.
    """
    set_env_vars(home, {"XORCISE_CATALOG_ENABLED": "true" if connected else "false"})
    get_settings.cache_clear()
    # The system view memoises its catalog probe for minutes (it is a remote round-trip). This
    # switch takes effect immediately, so without dropping that memo the Settings/dashboard
    # catalog readout would keep reporting the OLD connection state long after the operator
    # flipped it — the one moment they are watching for it to change.
    from xorcise.core.rest.system_view import reset_probe_cache

    reset_probe_cache()
    return build_config_view(get_settings())


def apply_model_update(home: Path, update: ModelConfigUpdate) -> ConfigView:
    """Persist the judge trio to `<home>/.env`, refresh settings, return the fresh masked view.

    A field left None is untouched; an empty string removes that env var (the upsert helper drops
    empty values), so passing `key=""` un-configures the judge.
    """
    env_updates: dict[str, str | None] = {}
    if update.key is not None:
        env_updates["XORCISE_MODEL_KEY"] = update.key
    if update.base_url is not None:
        env_updates["XORCISE_MODEL_BASE_URL"] = update.base_url
    if update.model_name is not None:
        env_updates["XORCISE_MODEL_NAME"] = update.model_name
    if update.timeout_seconds is not None:
        env_updates["XORCISE_MODEL_TIMEOUT_SECONDS"] = str(update.timeout_seconds)
    if update.transcript_max_tokens is not None:
        env_updates["XORCISE_JUDGE_TRANSCRIPT_MAX_TOKENS"] = str(update.transcript_max_tokens)
    if update.span_max_tokens is not None:
        env_updates["XORCISE_JUDGE_SPAN_MAX_TOKENS"] = str(update.span_max_tokens)
    if update.tokenizer is not None:
        env_updates["XORCISE_JUDGE_TOKENIZER"] = update.tokenizer
    set_env_vars(home, env_updates)
    get_settings.cache_clear()
    return build_config_view(get_settings())


def apply_terrain_model_update(home: Path, update: TerrainModelConfigUpdate) -> ConfigView:
    """Persist the terrain-model override to `<home>/.env` (empty string removes a var → that field
    falls back to the judge), refresh settings, return the fresh masked view."""
    env_updates: dict[str, str | None] = {}
    if update.key is not None:
        env_updates["XORCISE_TERRAIN_MODEL_KEY"] = update.key
    if update.base_url is not None:
        env_updates["XORCISE_TERRAIN_MODEL_BASE_URL"] = update.base_url
    if update.model_name is not None:
        env_updates["XORCISE_TERRAIN_MODEL_NAME"] = update.model_name
    if update.transcript_max_tokens is not None:
        env_updates["XORCISE_TERRAIN_TRANSCRIPT_MAX_TOKENS"] = str(update.transcript_max_tokens)
    set_env_vars(home, env_updates)
    get_settings.cache_clear()
    return build_config_view(get_settings())
