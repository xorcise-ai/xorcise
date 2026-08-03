"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { PanelLeft, PanelLeftClose } from "lucide-react";
import { cn } from "@/components/ui/cn";
import { useUiStore } from "@/stores/ui";
import { NAV_ITEMS, FOOTER_NAV, type NavItem } from "./nav-items";

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

function NavLink({
  item,
  active,
  collapsed,
}: {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      title={collapsed ? item.label : undefined}
      className={cn(
        "flex items-center gap-3 rounded-md px-2 py-1.5 text-body transition-colors",
        active
          ? "bg-sidebar-accent text-primary"
          : "text-sidebar-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
      )}
    >
      <Icon className="size-4 shrink-0" />
      {/* The label stays in the DOM at every width and is hidden visually, not
          removed. Two reasons: the accessible name no longer depends on `title`
          (which assistive tech may ignore and touch users can't hover), and the
          rail-vs-full decision below can be pure CSS at the breakpoint. */}
      <span className={collapsed ? "sr-only" : "sr-only lg:not-sr-only"}>
        {item.label}
      </span>
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggle = useUiStore((s) => s.toggleSidebar);

  return (
    <nav
      aria-label="Main"
      className={cn(
        "flex shrink-0 flex-col border-r border-sidebar-border bg-sidebar",
        // Width is a layout property, so the toggle re-lays-out <main> and every
        // trace/terrain child on each frame. Gate it behind motion-safe so the
        // Calm path (brand Module 09) skips the reflow entirely.
        "motion-safe:transition-[width]",
        // Brand §09: "<768 nav collapses to a top bar". Until a drawer exists, the
        // honest intermediate is an icon rail below lg — 46px instead of 208px,
        // which returns 162px of a 375px viewport to the actual application.
        // At lg and up the operator's own collapse preference wins.
        collapsed ? "w-12" : "w-12 lg:w-52",
      )}
    >
      <div className="flex flex-col gap-1 p-2">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.href}
            item={item}
            active={isActive(pathname, item.href)}
            collapsed={collapsed}
          />
        ))}
      </div>
      <div className="mt-auto flex flex-col gap-1 p-2">
        {FOOTER_NAV.map((item) => (
          <NavLink
            key={item.href}
            item={item}
            active={isActive(pathname, item.href)}
            collapsed={collapsed}
          />
        ))}
        <button
          type="button"
          onClick={toggle}
          aria-label="Toggle sidebar"
          className="flex items-center gap-3 rounded-md px-2 py-1.5 text-body text-sidebar-foreground transition-colors hover:bg-sidebar-accent/60 hover:text-foreground"
        >
          {collapsed ? (
            <PanelLeft className="size-4 shrink-0" />
          ) : (
            <PanelLeftClose className="size-4 shrink-0" />
          )}
          <span className={collapsed ? "sr-only" : "sr-only lg:not-sr-only"}>
            Collapse
          </span>
        </button>
      </div>
    </nav>
  );
}
