"""Frontend static-export build helper for the lifecycle commands (cli).

`xorcise up` serves the Next.js static export at ``/ui`` from
``core/frontend/_static`` (see ``core/rest/app.py:mount_ui``). That export is a
build artifact produced by ``frontend/package.json``'s ``build:static``
(``next build && rm -rf _static && cp -r out _static``). In a *source checkout*
it can be:

- **missing** — a fresh clone has no export yet because ``_static/`` is
  VCS-ignored;
- **broken** — ``index.html`` references chunk hashes that are not the files on
  disk (a partial / drifted build), so every chunk 404s and React never boots;
- **stale** — frontend source was edited since the last build.

``ensure_frontend_ready`` rebuilds the export on ``up`` when any of those hold,
so the operator never runs the build by hand and never meets a blank ``/ui``.

Installed wheels bake ``_static`` in at package time (``pyproject`` ``artifacts``)
and carry no ``frontend/`` source, so :func:`frontend_build_status` returns
``packaged`` and nothing is built.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from rich.console import Console

from xorcise.core.cli._shared import err_console

# The served export dir — mirrors ``_STATIC_DIR`` in ``core/rest/app.py`` (kept
# local so this CLI helper doesn't import the FastAPI app just to boot). From
# ``core/cli/_frontend.py``: parent.parent == ``core`` → ``frontend/_static``.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "_static"

# `_next` asset refs in the export's index.html, e.g.
# /ui/_next/static/chunks/app/page-<hash>.js  — the char class stops cleanly at
# the closing quote (or an escaped ``\``), so no trailing junk is captured.
_ASSET_RE = re.compile(r"/ui/(_next/static/[A-Za-z0-9._/-]+)")

_SKIP_ENV = "XORCISE_SKIP_FRONTEND_BUILD"

# Recognizable phase lines in `next build` output → the label the spinner shows.
# Cosmetic only: an unrecognized log (a new Next.js wording, npm noise) simply
# leaves the spinner on its generic label — never a wrong claim, never a crash.
_PHASE_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Creating an optimized production build"), "compiling"),
    (re.compile(r"Compiled successfully"), "compiled"),
    (re.compile(r"Linting and checking validity of types"), "checking types"),
    (re.compile(r"Collecting page data"), "collecting page data"),
    (re.compile(r"Generating static pages \((\d+)/(\d+)\)"), "generating pages ({0}/{1})"),
    (re.compile(r"Finalizing page optimization"), "finalizing"),
    (re.compile(r"Collecting build traces"), "collecting build traces"),
)


def build_phase(tail: str) -> str | None:
    """The most recent recognizable build phase in a chunk of `next build` log, else None.

    Last match wins — the log is append-only, so the marker furthest into the
    text is the phase the build is actually in (and `Generating static pages
    (9/14)` supersedes `(3/14)`)."""
    best: tuple[int, str] | None = None
    for pattern, label in _PHASE_MARKERS:
        for match in pattern.finditer(tail):
            if best is None or match.start() >= best[0]:
                best = (match.start(), label.format(*match.groups()))
    return best[1] if best else None


def _run_with_spinner(
    cmd: list[str], cwd: Path, log_path: Path, label: str, console: Console
) -> int:
    """Run `cmd` (output → `log_path`) behind a live spinner that tails the log.

    The spinner shows the real build phase parsed from the log (honest progress:
    indeterminate spinner + elapsed, never a fabricated bar) and vanishes when
    the process exits — the caller's permanent lines are the record."""
    from xorcise.core.cli._ux import live_spinner

    log_path.parent.mkdir(parents=True, exist_ok=True)
    start_offset = log_path.stat().st_size if log_path.exists() else 0
    with log_path.open("ab") as log:
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=log, stderr=log)
        try:
            with (
                live_spinner(console) as progress,
                log_path.open("r", encoding="utf-8", errors="replace") as feed,
            ):
                task = progress.add_task(label, total=None)
                feed.seek(start_offset)  # the log appends across runs — only THIS run's output
                tail = ""
                while proc.poll() is None:
                    time.sleep(0.15)
                    tail = (tail + feed.read())[-4000:]
                    phase = build_phase(tail)
                    if phase:
                        progress.update(task, description=f"{label} [dim]· {phase}[/dim]")
        except BaseException:
            # Mirror subprocess.run's guarantee on the path this replaces: the child is
            # killed and reaped before ANY exception (Ctrl-C included) propagates, so an
            # immediate `up` retry never races a still-running npm build over _static.
            proc.kill()
            proc.wait()
            raise
    return proc.wait()


def find_frontend_source(start: Path | None = None) -> Path | None:
    """Return the ``frontend/`` source dir (holding ``package.json``), else ``None``.

    Walks up from this file (a source checkout keeps ``__file__`` inside the repo,
    even under an editable install). ``None`` ⇒ an installed wheel with no source
    tree — the baked ``_static`` is authoritative and must not be rebuilt.
    """
    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        cand = parent / "frontend" / "package.json"
        if cand.is_file():
            return cand.parent
    return None


def referenced_assets(index_html: str) -> list[str]:
    """The ``_next/static/...`` asset paths referenced by the export's index.html."""
    return sorted(set(_ASSET_RE.findall(index_html)))


def missing_assets(static_dir: Path) -> list[str]:
    """Referenced assets whose files are absent under ``static_dir`` (empty ⇒ consistent)."""
    index = static_dir / "index.html"
    if not index.is_file():
        return []
    return [
        a
        for a in referenced_assets(index.read_text(encoding="utf-8"))
        if not (static_dir / a).is_file()
    ]


def newest_source_mtime(frontend_dir: Path) -> float:
    """Newest mtime among the build inputs (``src/**``, ``next.config.ts``, ``package.json``)."""
    inputs: list[Path] = [frontend_dir / "next.config.ts", frontend_dir / "package.json"]
    src = frontend_dir / "src"
    if src.is_dir():
        inputs.extend(p for p in src.rglob("*") if p.is_file())
    return max((p.stat().st_mtime for p in inputs if p.exists()), default=0.0)


def frontend_build_status(frontend_dir: Path | None, static_dir: Path) -> str:
    """Classify the export as ``packaged | missing | broken | stale | fresh`` (first match wins).

    See the module docstring for what each status means and triggers.
    """
    if frontend_dir is None:
        return "packaged"
    if not (static_dir / "_next").is_dir() or not (static_dir / "index.html").is_file():
        return "missing"
    if missing_assets(static_dir):
        return "broken"
    if newest_source_mtime(frontend_dir) > (static_dir / "index.html").stat().st_mtime:
        return "stale"
    return "fresh"


def ensure_frontend_ready(console: Console, static_dir: Path = _STATIC_DIR) -> None:
    """(Re)build the ``/ui`` static export on ``up`` when missing/broken/stale.

    Source-checkout only — an installed wheel (``packaged``) or an up-to-date
    export (``fresh``) is a silent no-op. Never raises: a UI build problem must
    not stop the server from booting. ``XORCISE_SKIP_FRONTEND_BUILD`` skips the
    whole step. On a fresh checkout it installs the frontend deps first (``npm
    ci`` when a lockfile is present, else ``npm install``) so ``next`` is on
    PATH. When a rebuild is needed but ``npm`` is unavailable — or the install
    or build fails — it warns with the manual fix and continues.
    """
    if os.environ.get(_SKIP_ENV):
        return
    frontend_dir = find_frontend_source()
    status = frontend_build_status(frontend_dir, static_dir)
    if status in ("packaged", "fresh"):
        return
    assert frontend_dir is not None  # a non-packaged status implies source is present

    fix = f"cd {frontend_dir} && npm run build:static"
    if shutil.which("npm") is None:
        console.print(f"[yellow]⚠ /ui needs a (re)build ({status}) but npm is missing.[/yellow]")
        console.print(f"[yellow]  run: {fix}[/yellow]")
        return

    # Without the frontend deps, `next` isn't on PATH and build:static dies with
    # "next: command not found". Gate on the `next` binary itself — not just node_modules/
    # existing — so an empty or partially-installed node_modules (e.g. a cleared or interrupted
    # install) still triggers a (re)install. Lockfile → reproducible `npm ci`, else `npm install`.
    # Raw npm/next output is the densest moment of a first run — keep it in a log
    # file (XORCISE_DEBUG=1 streams it) and speak in the CLI's own voice here.
    from xorcise.core.home import xorcise_home

    log_path = Path(xorcise_home()) / "frontend-build.log"
    debug = os.environ.get("XORCISE_DEBUG") == "1"

    def _run_logged(cmd: list[str], label: str) -> int:
        if debug:
            return subprocess.run(cmd, cwd=str(frontend_dir), check=False).returncode
        # An interactive stderr gets a spinner (with real phases tailed from the log) so
        # a minute-long npm step never reads as a hang. Progress rides stderr — the
        # mission-pull idiom — so redirected stdout stays clean; a fully piped run keeps
        # the historical line-based behaviour exactly.
        if err_console.is_terminal:
            return _run_with_spinner(cmd, frontend_dir, log_path, label, err_console)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            return subprocess.run(
                cmd, cwd=str(frontend_dir), check=False, stdout=log, stderr=log
            ).returncode

    if not (frontend_dir / "node_modules" / ".bin" / "next").exists():
        install = (
            ["npm", "ci"] if (frontend_dir / "package-lock.json").is_file() else ["npm", "install"]
        )
        console.print(f"installing frontend deps ({' '.join(install)})… (log: {log_path})")
        if _run_logged(install, "installing frontend deps") != 0:
            console.print(
                "[yellow]⚠ frontend dependency install failed — /ui may be blank.[/yellow]"
            )
            console.print(f"[yellow]  see {log_path}; fix with: {fix}[/yellow]")
            return

    console.print(f"rebuilding frontend UI ({status})… (log: {log_path})")
    started = time.monotonic()
    if _run_logged(["npm", "run", "build:static"], "rebuilding frontend UI") == 0:
        # Elapsed decorates the interactive view only — piped/captured stdout keeps
        # the historical exact line (scripts anchor on it). getattr: test stand-ins.
        if getattr(console, "is_terminal", False):
            console.print(f"frontend UI built ✓ [dim]({int(time.monotonic() - started)}s)[/dim]")
        else:
            console.print("frontend UI built ✓")
    else:
        console.print("[yellow]⚠ frontend build failed — /ui may be blank.[/yellow]")
        console.print(f"[yellow]  see {log_path}; fix with: {fix}[/yellow]")
