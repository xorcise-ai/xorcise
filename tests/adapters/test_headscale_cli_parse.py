from xorcise.core.headscale.cli import (
    DockerExecHeadscaleCli,
    HeadscaleCli,
    StubHeadscaleCli,
)


def test_real_adapter_is_a_headscale_cli():
    cli = DockerExecHeadscaleCli(container="headscale")
    assert isinstance(cli, HeadscaleCli)
    assert cli.container == "headscale"


def test_stub_cli_reports_a_version():
    # version() is the cheap reachability probe the rest layer uses; the stub
    # answers without Docker so unit/adapters lanes can exercise the seam.
    assert StubHeadscaleCli().version()
