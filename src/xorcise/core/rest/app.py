"""FastAPI app factory (application layer). Bare app + a /ui static mount helper.

Routers are included by roles/boot (the composition root) — NOT here — so this
factory has no eager part imports.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "_static"
# Emitted into the export by Next's app-dir `icon.svg` convention (frontend/src/app/icon.svg),
# which also injects the basePath-aware <link rel="icon"> into every exported page.
_ICON_NAME = "icon.svg"

# Shown at /ui before the first frontend build. The export is VCS-ignored build
# output, so a fresh checkout has no ``_static/index.html`` at all — this page is
# CODE, not a tracked artifact, and only exists so /ui explains itself instead of
# answering a bare 404 that reads as a broken install. `xorcise up` builds the
# real UI automatically and this page is never seen again.
_PLACEHOLDER_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>XORCISE</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #101010; color: #d4d4d4;
         font-family: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace; }
  main { text-align: center; padding: 2rem; }
  .mark { color: #e8b84b; }
  h1 { font-size: 1rem; font-weight: 600; letter-spacing: .35em; margin: 0 0 1rem; }
  p { margin: .35rem 0; font-size: .85rem; color: #8a8a8a; }
  code { color: #e8b84b; }
</style>
</head>
<body>
<main>
  <h1><span class="mark">⊕</span> XORCISE</h1>
  <p>The interface has not been built yet.</p>
  <p>Run <code>xorcise up</code> — it builds the UI automatically.</p>
</main>
</body>
</html>
"""


def create_app() -> FastAPI:
    """Return a bare FastAPI app (no routers mounted yet)."""
    return FastAPI(title="XORCISE", version="0")


def mount_ui(app: FastAPI) -> None:
    """Mount the generated static UI at /ui — or a generated placeholder before
    the first build — plus a root /favicon.ico.

    The UI lives under /ui, but a browser pointed at the API origin still probes
    ``/favicon.ico`` at the root — so serve the exported app icon there instead of
    logging a 404. The icon ships with the vendored export and is therefore absent
    in a source checkout that hasn't built the frontend; the route answers 404 in
    that case rather than failing at mount time.
    """
    if (_STATIC_DIR / "index.html").is_file():
        app.mount("/ui", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
    else:
        # No export yet (fresh checkout, or a cleared partial build): /ui still
        # answers, with the fix, rather than 404ing like a broken install.
        @app.get("/ui", include_in_schema=False)
        @app.get("/ui/", include_in_schema=False)
        def ui_placeholder() -> HTMLResponse:
            return HTMLResponse(_PLACEHOLDER_HTML)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        icon = _STATIC_DIR / _ICON_NAME
        if not icon.is_file():
            raise HTTPException(status_code=404, detail="No icon in this build")
        return FileResponse(icon, media_type="image/svg+xml")
