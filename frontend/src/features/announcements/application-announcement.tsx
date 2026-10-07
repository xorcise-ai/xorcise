"use client";

import { useCallback } from "react";
import { AnnouncementBanner } from "./announcement-banner";
import { useDismissal } from "./dismissal";
import { announcementFor, useAnnouncements } from "./queries";

/**
 * The application-wide announcement, rendered in the app shell under the header.
 *
 * There is no loading state and no error state, on purpose. The endpoint answers
 * `200 {"announcements": []}` for a disabled, unreachable, slow or malformed remote — absence
 * is the only failure this component can ever see, and rendering nothing is the correct
 * response to it. A skeleton would be a strip of grey chrome that appears on every page load
 * of an app that usually has no announcement at all, and an error row would tell the reader
 * about a service they did not ask about and cannot fix. Both would shift the layout under
 * the header for no information.
 */
export function ApplicationAnnouncement() {
  const { data } = useAnnouncements();
  const announcement = announcementFor(data, "application");
  return <Placement announcement={announcement} />;
}

/**
 * Split out so the dismissal hook is called unconditionally. `useDismissal` is a hook and
 * cannot sit behind the `if (!announcement) return null` above, and the id/revision it keys
 * on only exist once there is an announcement.
 */
function Placement({
  announcement,
}: {
  announcement: ReturnType<typeof announcementFor>;
}) {
  const [dismissed, dismissNow] = useDismissal(
    announcement?.id ?? "",
    announcement?.revision ?? 0,
  );
  const dismissAndKeepFocus = useCallback(() => {
    dismissNow();
    focusMainLandmark();
  }, [dismissNow]);
  // `&& dismissible`, because the dismissal record is the READER'S file, not ours: another
  // tab, an older build or a devtools console can write any id and revision into it. Honouring
  // it unconditionally meant one hand-written entry hid an active incident — the single banner
  // the remote parser and the banner component both go out of their way to make unclosable.
  // Storage may hide only what the publisher allowed to be hidden.
  if (!announcement || (dismissed && announcement.dismissible)) return null;
  return (
    <AnnouncementBanner
      announcement={announcement}
      variant="shell"
      onDismiss={dismissAndKeepFocus}
    />
  );
}

/**
 * Dismiss, then put focus somewhere it still exists.
 *
 * The close button is the focused element when this runs and unmounts with the banner an
 * instant later. With no target focus falls to <body>, where a keyboard or switch user's next
 * Tab restarts at the top of the document — past the skip link, the header and the whole
 * sidebar (WCAG 2.4.3, Focus Order). The app shell already gives <main> `tabIndex={-1}` so its
 * skip link can land there, which makes it the one focus target the shell guarantees exists;
 * `preventScroll` keeps dismissing a banner from also moving the page under the reader.
 */
function focusMainLandmark(): void {
  document.getElementById("main-content")?.focus({ preventScroll: true });
}
