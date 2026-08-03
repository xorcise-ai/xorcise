from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "xorcise"
ALLOWED = {
    Path("core/config/__init__.py"),  # the single source of the default host literal
    # The served join script's SOCKS5 proxy is the AGENT's own loopback (tailscaled
    # --socks5-server), not XORCISE server wiring — intrinsically 127.0.0.1, no deployment host.
    Path("core/runs/join.py"),
    # The rendered headscale config's metrics/gRPC listeners are the CONTAINER's own loopback
    # (never published; the provisioner CLI goes through docker exec + the unix socket), not
    # brain service wiring — intrinsically 127.0.0.1, no deployment host.
    Path("core/headscale/provision.py"),
}


def test_no_hardcoded_localhost_in_service_wiring():
    offenders = []
    for p in SRC.rglob("*.py"):
        if p.relative_to(SRC) in ALLOWED:
            continue
        text = p.read_text()
        if "127.0.0.1" in text or "localhost" in text:
            offenders.append(str(p.relative_to(SRC)))
    assert offenders == [], f"hardcoded localhost outside config: {offenders}"
