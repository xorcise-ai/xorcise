"use client";

import type { ReactNode } from "react";
import { Header } from "./header";
import { Sidebar } from "./sidebar";
import { StatusBar } from "./status-bar";
import { ServerUnreachable } from "./server-unreachable";
import { ToastHost } from "@/components/ui/toast";
import { RunNotificationsWatcher } from "@/features/runs/run-notifications";
import { useServerHealth } from "@/features/setup/queries";

export function AppShell({ children }: { children: ReactNode }) {
  const health = useServerHealth();
  const unreachable = health.isError;

  return (
    // h-dvh, not h-screen: 100vh excludes the mobile URL bar, which pushed the
    // StatusBar — the only persistent liveness signal — below the fold.
    <div className="flex h-dvh flex-col">
      {/* WCAG 2.4.1 (Level A). NAV_ITEMS + FOOTER_NAV render seven links before
          <main>, so without this a keyboard or switch user re-traverses the whole
          sidebar on every route change. Hidden until focused. */}
      <a
        href="#main-content"
        className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:left-3 focus-visible:top-3 focus-visible:z-50 focus-visible:rounded-md focus-visible:bg-primary focus-visible:px-3 focus-visible:py-1.5 focus-visible:text-dense focus-visible:text-primary-foreground"
      >
        Skip to content
      </a>
      <Header />
      {unreachable && <ServerUnreachable />}
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main
          id="main-content"
          tabIndex={-1}
          className="min-w-0 flex-1 overflow-auto focus-visible:outline-none"
        >
          {children}
        </main>
      </div>
      <StatusBar healthy={!unreachable} />
      {/* App-wide run-transition notifications: the watcher polls the shared
          ["runs"] query, the host renders the resulting toast stack. */}
      <RunNotificationsWatcher />
      <ToastHost />
    </div>
  );
}
