"use client";

import { AlertTriangle } from "lucide-react";

/** Clear, non-crashing banner shown when the server API is unreachable. */
export function ServerUnreachable() {
  return (
    <div
      role="alert"
      className="flex items-center gap-2 border-b border-err/30 bg-err/10 px-6 py-2 text-body text-err"
    >
      <AlertTriangle className="size-4 shrink-0" />
      <span>
        Can&apos;t reach the XORCISE server — is{" "}
        <code className="font-mono text-err">xorcise up</code> running? Retrying…
      </span>
    </div>
  );
}
