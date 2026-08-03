import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { HarnessCapabilityProfile } from "@/lib/api/types";

const CAPABILITIES_KEY = ["harness-capabilities"] as const;

/**
 * Declared adapter capabilities. `staleTime: Infinity` — this is static data (the same MR
 * that teaches an adapter a new span flips its profile in source, not at runtime), so once
 * fetched it never needs a background refetch for the life of the query client.
 */
export function useCapabilities() {
  const query = useQuery({
    queryKey: CAPABILITIES_KEY,
    queryFn: () =>
      api.get<HarnessCapabilityProfile[]>("/harnesses/capabilities"),
    staleTime: Infinity,
  });

  const byName = new Map(
    (query.data ?? []).map((profile) => [profile.adapter_name, profile]),
  );

  return { ...query, profiles: query.data, byName };
}
