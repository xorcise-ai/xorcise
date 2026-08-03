"""Hatchling build hook — build the /ui static export before the wheel is assembled.

Runs at BUILD time only (`uv build` / `python -m build` / `hatch build`), on the
maintainer/CI machine — never at `pip install` and never at runtime. It guarantees a
wheel can never ship a stale or missing UI: the export in
``src/xorcise/core/frontend/_static`` is a build artifact (VCS-ignored) that
``[tool.hatch.build.targets.wheel].artifacts`` force-includes verbatim, so whatever is
on disk when the wheel is zipped is what ships. This hook regenerates it first via
``npm run build:static``.

Degradation, in order:
- frontend source present + npm available → build the export (the normal path).
- no frontend source (e.g. a wheel built from an sdist that already carries ``_static``)
  → ship the bundled export if present, else fail (a wheel with no UI is not shippable).
- source present but npm missing → ship an existing export with a warning, else fail.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FrontendStaticExportBuildHook(BuildHookInterface):
    """Refresh core/frontend/_static from the Next.js source before packaging."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # Editable installs (`pip install -e .`, including CI's `uv pip install -e ".[dev]"`) build
        # the `editable` variant — they don't need a bundled export (dev serves the working-tree
        # _static and `xorcise up` rebuilds it). Only the real wheel (`version ==
        # "standard"`) must carry a fresh UI. Skipping non-standard builds also keeps a Node-less
        # dev/CI environment from failing the install.
        if version != "standard":
            return
        root = Path(self.root)
        frontend = root / "frontend"
        static = root / "src" / "xorcise" / "core" / "frontend" / "_static"
        built = static / "_next"

        if not (frontend / "package.json").is_file():
            # No source tree (typical when a wheel is built from an sdist that already
            # carries the export). Ship what's bundled; fail only if nothing is there.
            if built.is_dir():
                self.app.display_info(f"frontend: no source; shipping bundled export ({static})")
                return
            raise RuntimeError(
                "frontend source is absent and no built export exists at "
                f"{static}; cannot build a wheel without a UI (build from a full checkout)."
            )

        if shutil.which("npm") is None:
            if built.is_dir():
                self.app.display_warning(
                    "frontend: npm not on PATH; shipping the existing export (may be stale)."
                )
                return
            raise RuntimeError(
                "npm is required to build the frontend export but is not on PATH; "
                "install Node 20+ (or build where npm is available)."
            )

        if not (frontend / "node_modules").is_dir():
            self.app.display_info("frontend: installing npm dependencies (npm ci)…")
            subprocess.run(["npm", "ci"], cwd=str(frontend), check=True)

        self.app.display_info("frontend: building static export (npm run build:static)…")
        subprocess.run(["npm", "run", "build:static"], cwd=str(frontend), check=True)
        if not built.is_dir():
            raise RuntimeError(f"frontend: build completed but produced no export at {static}")
        self.app.display_info("frontend: static export built ✓")
