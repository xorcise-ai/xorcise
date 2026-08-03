# `app/` — routing

Next.js App Router. These files are **thin shells only**: layout, providers, and one entry per
route. All real logic lives in `../features/<domain>/` — a file here should wire a route to a
feature, not implement it.

## Routes

`layout.tsx` + `providers.tsx` wrap everything; `page.tsx` / `home-router.tsx` land you on the
dashboard; `not-found.tsx` is the fallback. The route folders: `agents/`, `missions/`, `runs/`
(new · live · result), `results/`, `setup/`, `settings/`.

## Conventions

- **Detail routes take `?query` params, not `[id]` segments.** The app is a static export
  served under the server's StaticFiles with no SPA fallback, so `/runs/live?id=…` deep-links
  and `/runs/[id]` would not. This is deliberate — see [`../../README.md`](../../README.md).
- Colocated `*.test.tsx` files cover routing behaviour (Vitest).
