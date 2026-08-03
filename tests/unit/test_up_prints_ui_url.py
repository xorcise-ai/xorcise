"""`xorcise up` must advertise the UI URL + the next-step actions.

Locks the operator-surface acceptance criterion that the `up` success banner
contains a resolvable UI URL and the register -> mission -> run next steps
(the CLI entry points whose effects are mirrored in the web UI).
"""

import pytest

from xorcise.core.cli.commands.lifecycle import next_steps_block
from xorcise.core.config import ui_url

pytestmark = pytest.mark.unit


def test_ui_url_resolves_to_the_rest_ui_path():
    url = ui_url()
    assert url.startswith("http://")
    assert url.endswith("/ui")


def test_up_next_steps_block_advertises_ui_url_and_actions():
    url = ui_url()
    block = next_steps_block(url)
    # the UI URL is printed
    assert url in block
    # and the three core next-step actions (CLI<->UI parity entry points)
    assert "Next steps" in block
    assert "agent register" in block
    assert "mission list" in block
    assert "run create" in block
