"""scripts/release_banner.py: the banner at the top of each release's notes."""

import importlib.util
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_banner.py"
SVG = "{http://www.w3.org/2000/svg}"

_spec = importlib.util.spec_from_file_location("release_banner", SCRIPT)
assert _spec is not None and _spec.loader is not None
banner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(banner)


def _version_group(tag: str) -> ET.Element:
    root = ET.fromstring(banner.render(tag))
    group = root.find(f".//{SVG}g[@id='version']")
    assert group is not None
    return group


def _translate_x_and_scale(element: ET.Element) -> tuple[float, float]:
    m = re.match(r"translate\(([-\d.]+),[-\d.]+\) scale\(([-\d.]+),", element.get("transform", ""))
    assert m is not None
    return float(m.group(1)), float(m.group(2))


def test_a_release_banner_is_an_svg_with_no_text_for_a_viewer_to_lack_a_font_for() -> None:
    root = ET.fromstring(banner.render("v0.2.0"))
    assert root.tag == f"{SVG}svg"
    assert root.findtext(f"{SVG}title") == "XORCISE.AI v0.2.0"
    assert root.findall(f".//{SVG}text") == []


def test_a_prerelease_banner_says_so() -> None:
    root = ET.fromstring(banner.render("v0.2.1rc1"))
    assert root.findtext(f"{SVG}title") == "XORCISE.AI v0.2.1rc1 (pre-release)"


@pytest.mark.parametrize("tag", ["v0.1.2", "v3.4.5", "v6.7.8", "v9.0.0a1", "v1.0.0b2", "v1.0.0rc3"])
def test_every_character_a_release_tag_can_hold_is_drawn(tag: str) -> None:
    assert len(_version_group(tag).findall(f".//{SVG}path")) == len(tag)


def test_a_long_tag_shrinks_and_stays_clear_of_the_lockup() -> None:
    short_x, short_scale = _translate_x_and_scale(_version_group("v0.2.0")[0])
    long_x, long_scale = _translate_x_and_scale(_version_group("v10.20.30rc12")[0])
    assert short_scale == 0.092  # the full 92 px
    assert long_scale < short_scale
    assert long_x > 360  # the lockup ends near x=335
    assert short_x > long_x


def test_the_script_and_the_release_workflow_accept_the_same_tags() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    m = re.search(r"grep -Eq '(\^v\[0-9\][^']+)'", workflow)
    assert m is not None
    assert m.group(1) == banner.TAG_RE.pattern


def test_the_glyph_file_covers_every_character_the_banner_sets() -> None:
    glyphs = json.loads((SCRIPT.parent / "release_banner_glyphs.json").read_text())
    for weight, chars in banner.CHARSETS.items():
        assert set(chars) <= set(glyphs["weights"][weight]), weight


def test_the_cli_refuses_anything_that_is_not_a_release_tag() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "v0.2"], capture_output=True, text=True)
    assert result.returncode == 2
    assert "not a release tag: 'v0.2'" in result.stderr
    assert result.stdout == ""
