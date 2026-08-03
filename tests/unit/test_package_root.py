import importlib
import subprocess
import sys


def test_version_is_importable():
    import xorcise

    assert isinstance(xorcise.__version__, str)
    assert xorcise.__version__


def test_top_level_import_has_no_side_effect_submodules():
    # Importing the distribution root must NOT eagerly pull in heavy submodules.
    code = (
        "import xorcise, sys; "
        "assert 'xorcise.core.rest' not in sys.modules, 'rest auto-imported'; "
        "assert 'xorcise.core.otel' not in sys.modules, 'otel auto-imported'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_core_is_importable():
    importlib.import_module("xorcise.core")
