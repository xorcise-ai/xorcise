"use client";

import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { Button } from "./button";
import { cn } from "./cn";
import { useExitTransition } from "./use-exit-transition";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const SIZE_CLS = {
  md: "max-w-md",
  lg: "max-w-2xl",
} as const;

/** Minimal controlled modal (static-export safe — no portal library). */
export function Dialog({
  open,
  onClose,
  title,
  children,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  size?: keyof typeof SIZE_CLS;
}) {
  const { mounted, closing } = useExitTransition(open);
  const panelRef = useRef<HTMLDivElement>(null);
  // Callers pass a fresh onClose every render. Read it through a ref so the effect below runs
  // only when the dialog opens or closes: re-running it on every render would pull focus back
  // to the panel whenever anything inside the dialog changed.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const shown = open && mounted;
  useEffect(() => {
    const panel = panelRef.current;
    if (!shown || !panel) return;
    // Focus moves into the dialog and stays there (Tab wraps), and returns to whatever opened
    // it on close, so a keyboard user is never left behind the scrim.
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panel.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      const first = items[0];
      const last = items[items.length - 1];
      const at = document.activeElement;
      if (!first || !last) {
        e.preventDefault();
      } else if (e.shiftKey && (at === first || at === panel)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (at === last || !panel.contains(at))) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [shown]);
  if (!mounted) return null;
  const requestClose = () => {
    if (!closing) onClose();
  };
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4 backdrop-blur-sm",
        closing ? "animate-backdrop-out" : "animate-backdrop-in",
      )}
      onClick={requestClose}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        className={cn(
          `w-full ${SIZE_CLS[size]} rounded-xl border border-border bg-card p-5 outline-none`,
          closing ? "animate-scale-out" : "animate-scale-in",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <h2 className="min-w-0 text-body font-bold text-heading">{title}</h2>
          {/* The shared ghost Button rather than a bare <button>: the hand-rolled one
              had no focus ring of its own and a 16px hit area — the icon itself. */}
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close"
            onClick={requestClose}
            className="-mr-1.5 -mt-1 shrink-0 text-text-tertiary hover:bg-transparent hover:text-foreground"
          >
            <X className="size-4" />
          </Button>
        </div>
        {children}
      </div>
    </div>
  );
}
