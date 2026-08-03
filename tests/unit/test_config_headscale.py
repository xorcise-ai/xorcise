from xorcise.core.config import Settings


def test_headscale_settings_have_defaults():
    s = Settings()
    assert s.headscale_container == "headscale"
    assert s.orchestrator_user == "orchestrator"
    assert s.router_tag == "tag:router"
    assert s.base_network == "10.200.0.0/16"
    assert s.cidr_prefix == 24
    assert s.key_expiration == "1h"


def test_headscale_settings_env_override(monkeypatch):
    monkeypatch.setenv("XORCISE_ROUTER_TAG", "tag:fence")
    monkeypatch.setenv("XORCISE_CIDR_PREFIX", "25")
    s = Settings()
    assert s.router_tag == "tag:fence"
    assert s.cidr_prefix == 25
