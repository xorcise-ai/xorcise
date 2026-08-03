import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_single_distribution_guard_passes():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_single_distribution.py")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
