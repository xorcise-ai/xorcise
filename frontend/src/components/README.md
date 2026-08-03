# `components/` — shared presentational

Cross-feature UI with **no domain logic**. Anything specific to agents, runs or missions
belongs in `../features/<domain>/`, not here.

- `ui/` — the presentational primitives (buttons, cards, tables, badges, and the like).
- `layout/` — app chrome shared across routes (navigation, page frames).

The test: if a component fetches data, knows a domain concept, or would only ever be used by
one feature, it does not live here.
