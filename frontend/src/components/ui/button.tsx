"use client";

import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

const buttonVariants = cva(
  // Sentence case, NOT uppercase. The Figma component board sets every button label in
  // sentence case ("Start run", "Configure", "Cancel", "Terminate") while badges, chips and
  // section eyebrows stay uppercase — that split is what keeps the two readable side by
  // side. We shipped `uppercase` on the base, which put action labels in the same register
  // as status chips and, at 11px with 0 tracking, turned every verb into a block of
  // even-height rectangles with no ascender or descender for the eye to catch. No call site
  // writes its label in caps, so removing the rule restores the authored string verbatim.
  "inline-flex items-center justify-center gap-2 rounded-md border font-mono text-caption transition-colors duration-[var(--dur-fast)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40",
  {
    variants: {
      variant: {
        // Primary — amber fill, dark text, one per view.
        default:
          "border-primary bg-primary font-bold text-on-primary hover:border-primary-hover hover:bg-primary-hover",
        // Ghost — amber-bordered, transparent; hover firms the border.
        outline:
          "border-primary/40 bg-transparent text-primary hover:border-primary",
        // Subtle — borderless, amber on hover.
        ghost:
          "border-transparent text-foreground hover:bg-raised hover:text-primary",
        // Destructive — red border, transparent fill.
        destructive:
          "border-destructive/40 bg-transparent text-destructive hover:border-destructive",
      },
      size: {
        sm: "h-7 px-2",
        default: "h-8 px-3.5",
        lg: "h-9 px-4",
        icon: "size-[30px]",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

// forwardRef so callers can move focus to a specific button — e.g. an inline confirmation that
// must land focus on Cancel rather than on the destructive action. Without it a `ref` is
// silently dropped and the focus call does nothing.
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  );
});

export { buttonVariants };
