import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";

export interface ServerHealth {
  status: string;
}

/** Poll the server's health every 5s; `isError` means the server is unreachable. */
export function useServerHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<ServerHealth>("/health"),
    refetchInterval: 5_000,
    retry: false,
  });
}
