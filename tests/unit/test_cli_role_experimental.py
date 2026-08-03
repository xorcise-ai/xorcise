"""Service roles are labelled EXPERIMENTAL wherever an operator meets them.

Only role `all` is a complete install. The others boot — which is precisely the hazard, since a
thing that starts looks like a thing that works. The role work shipped the ACTIVATION machinery
and said so explicitly ("not any role's real behaviour"), but nothing carried that to the
operator. Worst case, measured: `serve --role control` answers POST /runs with 201 and a real
image ref while launching no container at all, and the run sits at `created` forever.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401 — registers commands on the shared app
from xorcise.core.cli._shared import app
from xorcise.core.cli.commands.serve import _warn_experimental_role

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_role_list_marks_every_non_default_role_experimental() -> None:
    out = runner.invoke(app, ["role", "list"]).output
    assert "Maturity" in out
    assert "Ready" in out  # role `all`
    # control / runner / headscale / collector
    assert out.count("Experimental") >= 4


def test_role_list_explains_what_experimental_means() -> None:
    out = runner.invoke(app, ["role", "list"]).output
    assert "multi-machine deployment is unfinished" in out.lower()


def test_role_show_does_not_claim_to_report_a_running_server(monkeypatch) -> None:
    """`role show` reads XORCISE_ROLE from the environment, so it describes THIS SHELL. It used
    to be titled "Active service role", which let it confidently name a role nothing was running
    (verified: `XORCISE_ROLE=collector xorcise role show` said Collector while the live server
    served `all`)."""
    monkeypatch.setenv("XORCISE_ROLE", "collector")
    out = runner.invoke(app, ["role", "show"]).output
    assert "this shell" in out.lower()
    assert "xorcise system" in out  # where the running server's real role comes from
    assert "Experimental" in out


def test_serve_help_marks_role_experimental() -> None:
    out = runner.invoke(app, ["serve", "--help"]).output
    assert "EXPERIMENTAL" in out


@pytest.mark.parametrize("role", ["control", "runner", "headscale", "collector"])
def test_boot_warning_names_the_role_as_experimental(role, capsys) -> None:
    _warn_experimental_role(role)
    err = capsys.readouterr().err
    assert role in err
    assert "EXPERIMENTAL" in err


def test_boot_warning_says_control_does_not_actually_run_anything(capsys) -> None:
    # The single most important sentence in this change: on role `control` a run is accepted and
    # never executed. An operator must learn that at boot, not from a run that never starts.
    _warn_experimental_role("control")
    err = capsys.readouterr().err
    assert "STUB" in err.upper()
    assert "201" in err


def test_default_role_boots_without_a_warning(capsys) -> None:
    _warn_experimental_role("all")
    assert capsys.readouterr().err == ""
