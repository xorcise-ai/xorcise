"use client";

/** The brand mark: an amber crosshair, verbatim from the brand system (assets/mark-amber.svg). */
function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="2 2 36 36" aria-hidden="true" className={className}>
      <circle
        cx="20"
        cy="20"
        r="15"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
      />
      <path
        d="M20 8V32 M8 20H32"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function Header() {
  return (
    <header className="flex h-12 shrink-0 items-center gap-2.5 border-b border-border bg-card px-6">
      {/* The brand's horizontal lockup: mark left of wordmark, the dot always amber.
          20px is the mark's minimum size; the gap keeps the required clearspace (≥ its radius).
          Measured against Module 03: mark 20px (the stated floor), cross-to-ring 0.8,
          clearspace 11.0px against a required 8.3px — all compliant. */}
      <Mark className="size-5 shrink-0 text-primary" />
      <span
        aria-label="XORCISE.AI"
        /* §3.1 sets the letterforms at #f2ead6, and the brand's own tokens.css names
           the wordmark token explicitly "so it never inherits a general text token".
           This had inherited text-heading (#f0f0f0) — the exact mistake it warns of. */
        className="text-body font-bold tracking-[0.1em] text-wordmark"
      >
        XORCISE<span className="text-primary">.</span>AI
      </span>
    </header>
  );
}
