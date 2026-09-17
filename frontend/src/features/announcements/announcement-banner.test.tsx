import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import type { Announcement } from "@/lib/api/types";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { AnnouncementBanner } from "./announcement-banner";

const banner = (over: Partial<Announcement> = {}): Announcement => ({
  id: "a1",
  revision: 1,
  placement: "application",
  tone: "information",
  body_md: "Scheduled maintenance on Friday.",
  dismissible: true,
  ...over,
});

describe("AnnouncementBanner", () => {
  it("interrupts a screen reader for an incident and only for an incident", () => {
    // `alert` is an assertive live region: it cuts across whatever the reader is doing,
    // wherever they are on the page. Correct for "the service is down"; wrong for a banner
    // that appears on load and then just sits there, which is every other tone.
    const { unmount } = render(
      <AnnouncementBanner announcement={banner({ tone: "incident" })} variant="shell" />,
    );
    expect(screen.getByTestId("announcement-banner-application")).toHaveAttribute(
      "role",
      "alert",
    );
    unmount();

    for (const tone of ["information", "maintenance", "resolved"] as const) {
      const view = render(
        <AnnouncementBanner announcement={banner({ tone })} variant="shell" />,
      );
      expect(
        screen.getByTestId("announcement-banner-application"),
        `${tone} must announce politely`,
      ).toHaveAttribute("role", "status");
      view.unmount();
    }
  });

  it("carries a glyph AND a spelled-out label for every tone, never hue alone", () => {
    // The design system's rule is brightness + glyph. A reader who cannot separate the
    // incident red from the resolved green still gets the word and the icon shape.
    const expected = {
      information: "Information",
      maintenance: "Maintenance",
      incident: "Incident",
      resolved: "Resolved",
    } as const;

    for (const [tone, label] of Object.entries(expected)) {
      const view = render(
        <AnnouncementBanner
          announcement={banner({ tone: tone as Announcement["tone"] })}
          variant="shell"
        />,
      );
      const root = screen.getByTestId("announcement-banner-application");
      expect(within(root).getByText(label)).toBeInTheDocument();
      // The icon is decorative — the label beside it is the accessible text, so announcing
      // the glyph too would just read the tone twice.
      const icon = root.querySelector("svg");
      expect(icon, `${tone} renders an icon`).not.toBeNull();
      expect(icon).toHaveAttribute("aria-hidden");
      expect(icon).toHaveClass("size-4", "shrink-0");
      view.unmount();
    }
  });

  it("gives each tone its own token classes", () => {
    const view = render(
      <AnnouncementBanner announcement={banner({ tone: "incident" })} variant="shell" />,
    );
    expect(screen.getByTestId("announcement-banner-application")).toHaveClass(
      "border-err/30",
      "bg-err/[0.06]",
      "text-err",
    );
    view.unmount();

    render(<AnnouncementBanner announcement={banner({ tone: "resolved" })} variant="inline" />);
    expect(screen.getByTestId("announcement-banner-application")).toHaveClass(
      "border-ok/30",
      "bg-ok/[0.06]",
      "text-ok",
    );
  });

  it("renders no close control when the announcement is not dismissible", () => {
    render(
      <AnnouncementBanner
        announcement={banner({ tone: "information", dismissible: false })}
        variant="shell"
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Dismiss announcement" }),
    ).not.toBeInTheDocument();
  });

  it("refuses to make an incident dismissible even when the payload says it is", () => {
    // The remote parser forces `dismissible: false` for an incident, so this payload should
    // be impossible — which is exactly why the component must not depend on that. Anything
    // that builds an Announcement without going through that parser (a cached row, a second
    // source, a future endpoint) would otherwise ship a close control on an active outage.
    render(
      <AnnouncementBanner
        announcement={banner({ tone: "incident", dismissible: true })}
        variant="shell"
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Dismiss announcement" }),
    ).not.toBeInTheDocument();
    // …and it is still the loud one: the invariant must not have cost it its alert role.
    expect(screen.getByTestId("announcement-banner-application")).toHaveAttribute(
      "role",
      "alert",
    );
  });

  it("falls back to the information tone rather than throwing on an unknown one", () => {
    // `tone` is a closed union in the generated type, but the value came off the wire from a
    // service that ships independently of this build. A tone added upstream must degrade to a
    // banner in the wrong colour, never to a throw inside the root layout.
    const rogue = { ...banner(), tone: "apocalypse" as Announcement["tone"] };
    expect(() => render(<AnnouncementBanner announcement={rogue} variant="shell" />)).not.toThrow();
    const root = screen.getByTestId("announcement-banner-application");
    expect(within(root).getByText("Information")).toBeInTheDocument();
    // Quietest tone, so an unrecognised one under-claims rather than crying incident.
    expect(root).toHaveAttribute("role", "status");
    expect(root).toHaveClass("text-info");
  });

  it("calls back when the close control is used", () => {
    const onDismiss = vi.fn();
    render(
      <AnnouncementBanner announcement={banner()} variant="shell" onDismiss={onDismiss} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss announcement" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("renders the body through the restricted grammar, not as HTML", () => {
    render(
      <AnnouncementBanner
        announcement={banner({
          body_md: "Deploy is **frozen**. See [the runbook](https://docs.xorcise.ai/x).",
        })}
        variant="shell"
      />,
    );
    const root = screen.getByTestId("announcement-banner-application");
    expect(within(root).getByText("frozen").tagName).toBe("STRONG");
    const link = within(root).getByRole("link", { name: "the runbook" });
    expect(link).toHaveAttribute("href", "https://docs.xorcise.ai/x");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("dresses the shell variant as a full-width strip and the inline one as a card", () => {
    const view = render(<AnnouncementBanner announcement={banner()} variant="shell" />);
    // Matches ServerUnreachable directly above it: one bottom border, the page gutter.
    expect(screen.getByTestId("announcement-banner-application")).toHaveClass(
      "border-b",
      "px-6",
    );
    view.unmount();

    render(
      <AnnouncementBanner announcement={banner({ placement: "catalog" })} variant="inline" />,
    );
    // Matches the card banners in mission-detail.tsx, since it sits inside a tab panel.
    expect(screen.getByTestId("announcement-banner-catalog")).toHaveClass(
      "rounded-md",
      "border",
      "p-3",
    );
  });
});
