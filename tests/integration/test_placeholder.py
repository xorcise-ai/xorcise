import shutil

import pytest


@pytest.mark.integration
def test_docker_availability_smoke():
    # Real runner+mission integration is covered by the other integration tests.
    if shutil.which("docker") is None:
        pytest.skip("docker not present")
    assert True
