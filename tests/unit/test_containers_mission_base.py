from pathlib import Path

BASE = Path("containers/mission-base")


def test_dockerfile_is_dind_without_tailscale_baked_in():
    # Tailscale is NOT installed in the base image; it runs as a separate inner
    # container (the official image, baked into images.tar) in its own clean netns.
    df = (BASE / "Dockerfile").read_text()
    assert "FROM docker:" in df and "dind" in df
    assert "tailscale.com/install.sh" not in df  # no client baked into the mission base
    assert "entrypoint.sh" in df


def test_entrypoint_renders_override_and_brings_up_compose():
    ep = (BASE / "entrypoint.sh").read_text()
    assert "docker load" in ep
    assert "base64 -d" in ep  # renders the per-run net-override from env
    assert "net-override.yml" in ep
    assert "docker compose" in ep
    # Tailscale is no longer started as an outer-netns process; it's a compose service now.
    assert "tailscale up" not in ep
    assert "tailscaled --tun" not in ep


def test_entrypoint_writes_delivered_headscale_ca():
    # air-gapped: when a CA is delivered, the entrypoint writes it where the override
    # mounts it into the router.
    ep = (BASE / "entrypoint.sh").read_text()
    assert "XORCISE_HEADSCALE_CA_B64" in ep
    assert "headscale-ca.pem" in ep
