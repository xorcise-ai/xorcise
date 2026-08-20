"use client";

import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/components/ui/cn";

export function NotFoundState({
  title = "Not found",
  message = "That page doesn’t exist.",
  backHref = "/",
  backLabel = "Back to dashboard",
}: {
  title?: string;
  message?: string;
  backHref?: string;
  backLabel?: string;
}) {
  return (
    <div
      role="alert"
      className="flex min-h-[60vh] flex-col items-center justify-center gap-2 p-6 text-center"
    >
      {/* text-display is the scale's hero-figure rung and exists precisely to retire the
          raw text-3xl/text-4xl this shipped with; a 404 page has exactly one such figure.
          The role carries weight 700, so font-bold at the call site is redundant. */}
      <p className="text-display text-primary">404</p>
      <p className="text-lead text-heading">{title}</p>
      <p className="max-w-[68ch] text-body text-text-secondary">{message}</p>
      {/* Button's own styling, kept on a <Link> so Next still client-routes it — Button
          renders a <button>, which would break navigation. */}
      <Link
        href={backHref}
        className={cn(buttonVariants({ variant: "outline" }), "mt-2")}
      >
        {backLabel}
      </Link>
    </div>
  );
}
