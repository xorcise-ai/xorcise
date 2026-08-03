"use client";

import Link from "next/link";

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
      <p className="text-4xl font-bold text-primary">404</p>
      <p className="text-body font-semibold text-heading">{title}</p>
      <p className="max-w-[68ch] text-body text-text-secondary">{message}</p>
      <Link
        href={backHref}
        className="mt-2 rounded-md border border-border px-3 py-1.5 text-dense text-foreground transition-colors hover:border-[rgba(255,255,255,0.14)] hover:text-primary"
      >
        {backLabel}
      </Link>
    </div>
  );
}
