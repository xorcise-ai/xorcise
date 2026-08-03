import subprocess
import sys

from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401 — registers commands/callback on shared app
from xorcise.core.cli._shared import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "xorcise" in result.stdout


def test_help_lists_command_groups():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "up" in result.stdout
    assert "agent" in result.stdout


def test_python_dash_m_runs():
    result = subprocess.run(
        [sys.executable, "-m", "xorcise", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "xorcise" in result.stdout
