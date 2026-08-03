# XORCISE frontend (Next.js, App Router — static export)

The operator surface. Built once to a static export and served by the server's FastAPI app
at `/ui` — the same artifact works served locally, from a CDN, or pointed at a remote server.

## Hard constraints
- Static export only (`output: 'export'`, no Node at runtime). NO SSR, RSC server work,
  route handlers, middleware, server actions, or the default `next/image` loader.
- The `/api` base resolves at RUNTIME (`window.__XORCISE_API_BASE__`, else `<origin>/api`) —
  never baked in at build time. That is what makes one export location-transparent.
- `src/lib/api/schema.ts` is GENERATED from the backend OpenAPI (`frontend/openapi.json`)
  and must never be hand-edited. Regenerate with `npm run codegen` after a backend
  contract change, and commit both files together.
- Detail, live and result routes take `?query` params rather than `[id]` segments, so the
  export deep-links under the server's StaticFiles with no SPA fallback.

## Layout
- `src/app/` — routing only (thin shells): layout, providers, dashboard, not-found, plus
  agents · missions · runs (new/live/result) · results · setup · settings.
- `src/features/<domain>/` — domain logic (components, hooks, `queries.ts`).
- `src/components/` — cross-feature presentational (`ui/` + `layout/`).
- `src/lib/api/` — generated schema and the single typed client chokepoint; plus
  `query-client`, `runtime-config`, `utils`.
- `src/stores/` — Zustand UI state only. Server state stays in TanStack Query.
- `src/test/` — MSW handlers and `renderWithProviders`. `e2e/` — Playwright.

## Data
- TanStack Query v5 for server state; `refetchInterval` for run status.
- The live trace POLLS `GET /runs/{id}/traces?since=<seq>` with an incremental cursor.
  There is no streaming endpoint — that is a deliberate simplification, not an oversight.

## Commands
```
npm run dev        # dev server
npm run typecheck  # tsc --noEmit
npm run test       # Vitest + MSW
npm run test:e2e   # Playwright (needs a running server)
npm run codegen    # regenerate src/lib/api/schema.ts from openapi.json
```

## Build into the Python package
`npm run build:static` runs `next build` and copies `out/` to
`src/xorcise/core/frontend/_static/`. The packaging build hook (`hatch_build.py`) does this
automatically when a wheel is built, so a release can never ship a stale UI.
