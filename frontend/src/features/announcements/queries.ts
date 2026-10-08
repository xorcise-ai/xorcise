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
 * `retry` is BOUNDED rather than off, and the reasoning that had it off was backwards.
 * `routers/announcements.py` returns `list_announcements(...)` unconditionally, and that
 * absorbs every remote problem into `200 {"announcements": []}`. So a non-200 or a transport
 * error reaching this hook is never the remote being unreachable — by construction it can only
 * be the LOCAL server: not up yet, restarting, or erroring. That is exactly the condition a
 * retry improves. Treating it as "the remote is unreachable, already handled" conflated two
 * different signals arriving on one channel, and left a tab silently blind for the life of the
 * document after a single blip — `xorcise down && xorcise up` underneath it was enough (#162).
 *
 * Silently matters: an absent banner is indistinguishable from nothing being published, so the
 * reader cannot tell a broken feature from a quiet one. For something whose job is incident
 * banners that is the wrong way to fail.
 *
 * Bounded is the other half. Three attempts ride out a restart; after that it stays absorbed
 * and stops, so a server that is genuinely gone does not turn this into a poller. The cost is
 * at most two extra requests, only on a document load whose first request already failed.
 *
 * `retryOnMount: false` stays, and is the one that is easy to miss. `refetchOnMount` only
 * governs a query that HAS data; a query sitting on an error is refetched by every newly
 * mounting observer regardless. This feature mounts two observers at different moments — the
 * shell banner immediately, the catalog banner when the Remote tab first renders — so against
 * a failing endpoint the default issued a fresh request as the catalog opened, on top of the
 * retries above. Off, the bounded attempts are the whole budget.
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
    // Bounded: enough to outlast a restart, not enough to poll. See the note above for why a
    // failure that reaches this hook is always local, and therefore always worth one more try.
    //
    // `retryDelay` is deliberately NOT pinned here, unlike every other knob in this block. The
    // library's exponential backoff is the right shape for riding out a restart, and the reason
    // the others are written out — that a library default could quietly turn this into a poller
    // — does not apply to a delay when the attempt COUNT is already bounded above. Leaving it
    // unset also lets a caller's QueryClient collapse it, which is what the tests do.
    retry: 2,
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
