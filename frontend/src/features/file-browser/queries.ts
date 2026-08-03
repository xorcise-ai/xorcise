import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { FsListing } from "@/lib/api/types";

/**
 * Read-only listing of one directory on the server host (local-only app).
 * `path === null` lists the user's home directory (the server's default).
 */
export function useFsList(path: string | null, enabled = true) {
  return useQuery({
    queryKey: ["fs", path ?? "~home"],
    queryFn: () =>
      api.get<FsListing>(
        `/fs/list${path ? `?path=${encodeURIComponent(path)}` : ""}`,
      ),
    enabled,
  });
}
