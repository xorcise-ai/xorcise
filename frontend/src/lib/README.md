# `lib/` — the client contract

Shared, framework-free plumbing. The important part is `api/`: the single chokepoint through
which the UI talks to the server.

## `api/`

- `schema.ts` — **GENERATED** from the backend OpenAPI (`frontend/openapi.json`). Never edit it
  by hand. Regenerate with `npm run codegen` after a backend contract change, and commit
  `openapi.json` and `schema.ts` together so the UI↔server contract cannot drift.
- `client.ts` — the typed client every feature calls; the one place a backend request is made.
- `runtime-config.ts` — resolves the `/api` base at **runtime**
  (`window.__XORCISE_API_BASE__`, else `<origin>/api`). This is what makes one static export
  work served locally, from a CDN, or pointed at a remote server.
- `query-client.ts` — the shared TanStack Query client. `format.ts` / `types.ts` / `utils` —
  presentation helpers and shared types.

Colocated `*.test.ts` files cover the client, formatting, and location-transparency behaviour.
