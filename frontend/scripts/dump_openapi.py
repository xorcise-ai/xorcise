"""Dump the server's OpenAPI schema to frontend/openapi.json (codegen input).

The frontend's typed API layer (src/lib/api/schema.ts) is GENERATED from this
snapshot via `npm run codegen` (openapi-typescript). Commit the snapshot so CI
can regenerate and `git diff --exit-code` as the no-drift gate.

Run from the repo root:  uv run python frontend/scripts/dump_openapi.py
"""

from __future__ import annotations

import json
import pathlib

from xorcise.core.roles.boot.role_all import build_rest_app


def main() -> None:
    app = build_rest_app()
    schema = app.openapi()
    out = pathlib.Path(__file__).resolve().parent.parent / "openapi.json"
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
