import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { HarnessDescriptor } from "@/lib/api/types";

const HARNESSES_KEY = ["harness-descriptors"] as const;

/** Static registration descriptors. They change only when the backend release changes. */
export function useHarnessDescriptors() {
  const query = useQuery({
    queryKey: HARNESSES_KEY,
    queryFn: () => api.get<HarnessDescriptor[]>("/harnesses"),
    staleTime: Infinity,
  });
  const byKind = new Map((query.data ?? []).map((descriptor) => [descriptor.kind, descriptor]));
  return { ...query, descriptors: query.data, byKind };
}
