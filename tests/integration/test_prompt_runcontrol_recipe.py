import re

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("delivery", ["launch-block", "prompt-only"])
def test_request_built_from_prompt_authenticates_and_hits_artifacts(
    migrated_home, delivery: str
) -> None:
    """An agent following ONLY the rendered prompt can submit.

    Parse the prompt for the Bearer header + the /api/runs/{id} path (as a prompt-only agent
    would), then POST /artifacts against the in-process app. The host is environment-specific
    (host.docker.internal), so apply the parsed path + header to the TestClient.

    Both deliveries of the token (#172): an agent started from a launch block reads it from
    $XORCISE_RUN_BEARER, which the block exports; one handed the prompt alone gets it inlined.
    """
    from xorcise.core import runs
    from xorcise.core.contracts.connect import ConnectArtifact, ConnectTarget
    from xorcise.core.roles.boot.role_all import build_rest_app
    from xorcise.core.runs.prompt import (
        RUN_BEARER_ENV,
        assemble_mission_prompt,
        inline_run_bearer,
        render_prompt_text,
    )

    run = runs.create_run(agent_id="a1", mission="webby", run_control_key="K", budget_seconds=600)
    mission = assemble_mission_prompt(
        run_id=run.run_id,
        mission="webby",
        objective="x",
        login_server="https://headscale.local:8443",
        join_key="tskey",
        run_control_url=f"http://host.docker.internal:3001/api/runs/{run.run_id}",
        run_control_key="K",
        targets=[ConnectTarget(name="web", host="10.200.1.10")],
        artifacts=[ConnectArtifact(name="flag", required=True)],
    )
    text = render_prompt_text(mission)
    if delivery == "prompt-only":
        text = inline_run_bearer(text, "K")

    # Parse the recipe exactly as a prompt-only agent would.
    # Anchored on the Header line: the recipes above it also spell out the header (as printf %s).
    bearer_match = re.search(r"Header:\s+Authorization: Bearer (\S+)", text)
    path_match = re.search(r"/api/runs/\S+", text)
    assert bearer_match and path_match
    bearer = bearer_match.group(1)
    if delivery == "launch-block":
        assert bearer == f"${RUN_BEARER_ENV}"  # the prompt names the variable, never the token
        bearer = "K"  # what the launch block exports it as
    base_path = path_match.group(0).rstrip("/")

    c = TestClient(build_rest_app())
    resp = c.post(
        f"{base_path}/artifacts",
        json={"name": "flag", "content": "XORCISE{x}"},
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert resp.status_code == 200
    # the old (wrong) header is rejected — proves the prompt's header is the required one
    assert (
        c.post(
            f"{base_path}/artifacts",
            json={"name": "flag", "content": "x"},
            headers={"X-Run-Key": bearer},
        ).status_code
        == 401
    )
