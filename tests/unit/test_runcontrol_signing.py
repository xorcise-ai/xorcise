import pytest

from xorcise.core.runcontrol.signing import sign, signed_path, verify

pytestmark = pytest.mark.unit


def test_sign_verify_roundtrip() -> None:
    sig = sign("secret", "r1", "dump.pcap", 1000)
    assert verify("secret", "r1", "dump.pcap", 1000, sig) is True


def test_verify_rejects_tampered_name() -> None:
    sig = sign("secret", "r1", "dump.pcap", 1000)
    assert verify("secret", "r1", "OTHER.bin", 1000, sig) is False


def test_verify_rejects_tampered_expiry_and_wrong_secret() -> None:
    sig = sign("secret", "r1", "dump.pcap", 1000)
    assert verify("secret", "r1", "dump.pcap", 9999, sig) is False
    assert verify("OTHER", "r1", "dump.pcap", 1000, sig) is False


def test_signed_path_contains_exp_and_sig() -> None:
    path = signed_path("secret", "r1", "dump.pcap", 1000)
    assert (
        path.startswith("/runs/r1/attachments/dump.pcap?") and "sig=" in path and "exp=1000" in path
    )
