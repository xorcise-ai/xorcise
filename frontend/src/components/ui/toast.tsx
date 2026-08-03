"use client";

import Link from "next/link";
import { X } from "lucide-react";
import { useToastStore, type ToastTone } from "@/stores/toasts";
import { cn } from "./cn";

/** Toast tone → accent colour (same palette as the run-card status accents). */
const toneAccent: Record<ToastTone, string> = {
  ok: "var(--color-ok)",
  err: "var(--color-err)",
  warn: "var(--color-primary)",
  info: "var(--color-primary)", // quiet amber default (guide §05)
};

/** Global toast stack — fixed bottom-right, above the StatusBar. Static-export
 *  safe (no portal library, mirrors ui/dialog.tsx). aria-live polite so screen
 *  readers announce run transitions without stealing focus. */
export function ToastHost() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-10 right-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cn(
            "rounded-r-md border border-border border-l-2 bg-raised px-3.5 py-3 font-mono",
            t.leaving ? "animate-slide-out-right" : "animate-slide-in-right",
          )}
          style={{ borderLeftColor: toneAccent[t.tone] }}
        >
          <div className="flex items-start justify-between gap-2">
            <p className="min-w-0 text-dense text-heading">
              {t.title}
            </p>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
              className="shrink-0 text-text-tertiary transition-colors hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          </div>
          {t.body && (
            <p className="mt-2 text-caption text-text-secondary">{t.body}</p>
          )}
          {t.href && (
            <Link
              href={t.href}
              onClick={() => dismiss(t.id)}
              className="mt-2 inline-block text-dense text-primary hover:underline"
            >
              {t.hrefLabel ?? "View"}
            </Link>
          )}
        </div>
      ))}
    </div>
  );
}
