# `features/<domain>/` — domain logic

One folder per product domain. A feature owns its components, hooks, and its `queries.ts`
(the TanStack Query hooks that talk to the server). Routes in `../app/` are thin shells that
render these; shared presentational primitives come from `../components/`.

Domains today: `agents`, `runs`, `missions`, `dashboard`, `live-trace`, `replay`, `results`,
`file-browser`, `capabilities`, `setup`, `settings`.

## Conventions

- **Server state lives in TanStack Query** (`queries.ts`), never in a store. `../stores/` holds
  ephemeral UI state only.
- All server calls go through the single typed client in `../lib/api/` — never `fetch` a
  backend route directly, so the generated OpenAPI contract stays the one source of truth.
- The live trace **polls** `GET /runs/{id}/traces?since=<seq>` with an incremental cursor;
  there is no streaming endpoint, and that is deliberate (see [`../../README.md`](../../README.md)).
