import pytest

from xorcise.core.contracts.runcontrol import (
    CompleteResponse,
    GetAttachmentResponse,
    IntelResponse,
    MissionInfo,
    SubmitArtifactRequest,
    SubmitArtifactResponse,
)

pytestmark = pytest.mark.unit


def test_mission_info_roundtrip() -> None:
    info = MissionInfo(mission="webby", objective="pop the box", attachments=("dump.pcap",))
    assert MissionInfo.model_validate(info.model_dump()) == info


def test_submit_artifact_request_requires_name_and_content() -> None:
    req = SubmitArtifactRequest(name="exploit.py", content="print('pwn')")
    assert req.name == "exploit.py"
    with pytest.raises(ValueError):
        SubmitArtifactRequest.model_validate({"name": "x"})  # content missing


def test_intel_response_carries_optional_intel() -> None:
    assert IntelResponse(intel=None, remaining=0).intel is None
    assert IntelResponse(intel="look at the headers", remaining=2).remaining == 2


def test_get_attachment_response_shape() -> None:
    from datetime import UTC, datetime

    r = GetAttachmentResponse(
        name="dump.pcap",
        url="/runs/r1/attachments/dump.pcap?exp=123&sig=abc",
        expires_at=datetime(2026, 6, 25, tzinfo=UTC),
        media_type="application/vnd.tcpdump.pcap",
        sha256=None,
    )
    assert GetAttachmentResponse.model_validate(r.model_dump(mode="json")) == r


def test_complete_response_defaults_terminal() -> None:
    assert CompleteResponse().state == "terminal"


def test_submit_artifact_response_defaults_accepted() -> None:
    r = SubmitArtifactResponse(name="x")
    assert r.accepted is True and r.name == "x"
