"use client";

import { X } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "./button";
import { cn } from "./cn";
import { useExitTransition } from "./use-exit-transition";

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
        "fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4",
        closing ? "animate-backdrop-out" : "animate-backdrop-in",
      )}
      onClick={requestClose}
    >
      <div
        className={cn(
          `w-full ${SIZE_CLS[size]} rounded-xl border border-border bg-card p-5`,
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
