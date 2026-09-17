import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { Announcement, AnnouncementsResponse } from "@/lib/api/types";

const ANNOUNCEMENTS_KEY = ["announcements"] as const;

/**
 * The active announcement banners, fetched ONCE per browser document load.
 *
 * "Once per document load" is the product requirement, not an optimisation. An announcement
 * is a banner somebody publishes centrally; if it could appear, change or vanish under a
 * reader who is mid-task, the page would rewrite itself around them for reasons they did not
 * cause and cannot see. So a published or withdrawn banner reaches an open tab only when its
 * reader deliberately refreshes. `queries.test.tsx` is the executable form of that sentence.
 *
 * Both placement components call this hook and share ANNOUNCEMENTS_KEY, so the second
 * consumer costs zero extra requests: one request serves the application banner and the
 * catalog banner together.
 *
 * Every refetch knob below is pinned EXPLICITLY, including the ones that happen to match the
 * current TanStack Query defaults (`refetchInterval`, `refetchIntervalInBackground`) and the
 * one the shared client in `lib/api/query-client.ts` already sets (`refetchOnWindowFocus`).
 * Nothing here is redundant: the shared defaults set only staleTime/retry/refetchOnWindowFocus,
 * so every other line is a library default, and a library default is exactly the kind of thing
 * that changes in a minor release. Written out, a future upgrade that flips one cannot quietly
 * turn this feature into a poller — it has to delete a line somebody wrote on purpose.
 *
 * `retry: false` is part of the same contract rather than an error-handling choice: the
 * endpoint answers `200 {"announcements": []}` for a disabled, unreachable, slow or malformed
 * remote, so a non-200 is not a condition a retry can improve, and a retry would be a second
 * request.
 *
 * `retryOnMount: false` is the one that is easy to miss, and the counter test is what found
 * it. `refetchOnMount` only governs a query that HAS data; a query that has never loaded and
 * is sitting on an error is refetched by every newly mounting observer regardless. This
 * feature mounts two observers at different moments — the shell banner immediately, the
 * catalog banner when the Remote tab first renders — so against a failing endpoint the
 * default issued a second request as the catalog opened. Off, the failure is absorbed once
 * and stays absorbed, which is the same promise the success path makes.
 */
export function useAnnouncements() {
  return useQuery({
    queryKey: ANNOUNCEMENTS_KEY,
    queryFn: () => api.get<AnnouncementsResponse>("/announcements"),
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: false,
    refetchIntervalInBackground: false,
    retry: false,
    retryOnMount: false,
  });
}

/**
 * The announcement for one placement, or undefined.
 *
 * The server sends at most one entry per placement, so this is a lookup and not a list
 * render. It tolerates `undefined` data (the single in-flight fetch, and every failure mode —
 * both of which mean "no banner", never "an error to show").
 *
 * `announcements` is optional-chained too, even though the generated type says it is always
 * present. The type describes what OUR backend returns; what arrives is whatever answered on
 * `/api`, which can be a canned fixture, a proxy, a mismatched build or an older deployment.
 * A 200 whose JSON simply lacks the key would make `.find` throw — and this runs in the app
 * shell, inside the root layout, so that TypeError would take down every route in the app
 * rather than costing one banner. Decoration must never do that.
 */
export function announcementFor(
  data: AnnouncementsResponse | undefined,
  placement: Announcement["placement"],
): Announcement | undefined {
  return data?.announcements?.find((a) => a.placement === placement) ?? undefined;
}
