import xorcise.core.headscale as hs


def test_public_surface():
    for name in (
        "NetworkController",
        "RunNetwork",
        "HeadscaleCli",
        "StubHeadscaleCli",
        "DockerExecHeadscaleCli",
        "HeadscaleError",
        "render_policy",
        "assert_policy_safe",
        "allocate_cidr",
        "cidr_for_index",
    ):
        assert hasattr(hs, name), name
