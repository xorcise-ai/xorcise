"use client";

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
  if (!announcement || dismissed) return null;
  return (
    <AnnouncementBanner
      announcement={announcement}
      variant="shell"
      onDismiss={dismissNow}
    />
  );
}
