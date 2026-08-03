"use client";

import type { InputHTMLAttributes } from "react";
import { cn } from "./cn";

export function Input({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        // border-input, not border-border: the edge is the only thing identifying
        // this control — its bg-deepest fill is 1.15:1 against the card behind it —
        // so it answers to WCAG 1.4.11's 3:1. border-border is for decorative
        // container rules and sits deliberately far below that floor.
        "h-8 w-full rounded-md border border-input bg-deepest px-2.5 font-mono text-dense text-foreground placeholder:text-text-tertiary focus-visible:border-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/30",
        className,
      )}
      {...props}
    />
  );
}
