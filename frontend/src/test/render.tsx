import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";

function testQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

// Render a component tree wrapped in a fresh QueryClient (retries disabled so
// error states assert immediately). Mirrors the app's <Providers>.
//
// `rerender` is wrapped so `result.rerender(<NewUi />)` re-wraps in the SAME QueryClientProvider
// (RTL's own `rerender` would otherwise replace the whole tree with the bare `ui`, dropping the
// provider and breaking any `useQuery` in it) — this lets a test swap props on a live query-bound
// component (e.g. a changed `selectedEventId`) without remounting the client.
export function renderWithProviders(ui: ReactElement) {
  const client = testQueryClient();
  const result = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return {
    ...result,
    rerender: (nextUi: ReactElement) =>
      result.rerender(<QueryClientProvider client={client}>{nextUi}</QueryClientProvider>),
  };
}

/** Stable wrapper for renderHook() hook tests (one client for the test). */
export function createQueryWrapper() {
  const client = testQueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}
