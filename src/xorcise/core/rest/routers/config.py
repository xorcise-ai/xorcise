"""Config router — fixed-shape local config surface.

GET returns the masked view (never the raw judge key); PUT writes the judge trio to ~/.xorcise/.env
and clears the settings cache so the next run is judged with the new model. Only the judge trio is
writable — the generic Settings model is deliberately NOT exposed for edit.
"""

from __future__ import annotations

from fastapi import APIRouter

from xorcise.core.config import get_settings
from xorcise.core.contracts.config import (
    CatalogConfigUpdate,
    ConfigView,
    JudgeTestResult,
    ModelConfigUpdate,
    NetworkConfigUpdate,
    TerrainModelConfigUpdate,
)
from xorcise.core.home import xorcise_home
from xorcise.core.rest.config_view import (
    apply_catalog_update,
    apply_model_update,
    apply_network_update,
    apply_terrain_model_update,
    build_config_view,
    run_judge_live_test,
    run_terrain_live_test,
)

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
def get_config() -> ConfigView:
    return build_config_view(get_settings())


@router.put("/model")
def put_model_config(update: ModelConfigUpdate) -> ConfigView:
    return apply_model_update(xorcise_home(), update)


@router.post("/model/test")
def test_model_config() -> JudgeTestResult:
    """Live-check the saved judge key by actually calling the model (a minimal completion).

    `configured` in GET /config is only a presence check; this proves the key works. Runs only
    on this explicit action so GET /config stays fast and spends no tokens on page load."""
    return run_judge_live_test(get_settings())


@router.put("/catalog")
def put_catalog_config(update: CatalogConfigUpdate) -> ConfigView:
    """Connect/disconnect the XORCISE remote catalog (the Settings switch)."""
    return apply_catalog_update(xorcise_home(), update.connected)


@router.put("/network")
def put_network_config(update: NetworkConfigUpdate) -> ConfigView:
    """Set the distributed-mode network addresses (Headscale URL / advertise host).

    Persisted to ~/.xorcise/.env; applies at the next server start (the GUI notes this)."""
    return apply_network_update(xorcise_home(), update)


@router.put("/terrain-model")
def put_terrain_model_config(update: TerrainModelConfigUpdate) -> ConfigView:
    """Set (or clear, via empty strings) the terrain-attribution model override. Defaults to the
    judge model when unset."""
    return apply_terrain_model_update(xorcise_home(), update)


@router.post("/terrain-model/test")
def test_terrain_model_config() -> JudgeTestResult:
    """Live-check the terrain attribution model by actually calling it (a minimal completion).

    Exercises the effective config — the custom (advanced-mode) override if set, else the judge
    trio — so the Settings UI can confirm a custom terrain model is reachable. Runs only on this
    explicit action so GET /config stays fast and spends no tokens on page load."""
    return run_terrain_live_test(get_settings())
