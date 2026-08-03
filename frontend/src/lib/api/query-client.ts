import { QueryClient } from "@tanstack/react-query";

/** A QueryClient with server-friendly defaults (short stale time, one retry). */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  });
}
