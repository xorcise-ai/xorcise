import re

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_request_built_from_prompt_authenticates_and_hits_artifacts(migrated_home) -> None:
    """An agent following ONLY the rendered prompt can submit.

    Parse the prompt for the Bearer header + the /api/runs/{id} path (as a prompt-only agent
    would), then POST /artifacts against the in-process app. The host is environment-specific
    (host.docker.internal), so apply the parsed path + header to the TestClient.
    """
    from xorcise.core import runs
    from xorcise.core.contracts.connect import ConnectArtifact, ConnectTarget
    from xorcise.core.roles.boot.role_all import build_rest_app
    from xorcise.core.runs.prompt import (
        assemble_mission_prompt,
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

    # Parse the recipe exactly as a prompt-only agent would.
    bearer_match = re.search(r"Authorization: Bearer (\S+)", text)
    path_match = re.search(r"/api/runs/\S+", text)
    assert bearer_match and path_match
    bearer = bearer_match.group(1)
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
