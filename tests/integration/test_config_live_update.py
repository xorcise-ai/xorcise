import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_put_config_model_takes_effect_without_restart(tmp_path, monkeypatch):
    """A model set against the RUNNING server is honored at the next grade, no restart."""
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.delenv("XORCISE_MODEL_KEY", raising=False)

    from xorcise.core import config as cfg
    from xorcise.core.rest.grade_assembly import build_eval_judge
    from xorcise.core.roles.boot.role_all import build_rest_app

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    c = TestClient(build_rest_app())

    # server boots with no judge: GET reports unconfigured, and the grade path resolves no model
    assert c.get("/api/config").json()["judge"]["configured"] is False
    assert build_eval_judge()._d.model is None

    # operator configures the judge against the RUNNING server (the path the CLI now uses)
    resp = c.put(
        "/api/config/model",
        json={
            "key": "sk-test",
            "base_url": "http://h:8000/v1",
            "model_name": "Qwen3.6-27B-FP8",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["judge"]["configured"] is True

    # no restart: GET now reflects it AND the grade path resolves a model
    assert c.get("/api/config").json()["judge"]["model_name"] == "Qwen3.6-27B-FP8"
    assert build_eval_judge()._d.model is not None

    cfg.get_settings.cache_clear()  # don't leak the tmp-home cache into other tests
