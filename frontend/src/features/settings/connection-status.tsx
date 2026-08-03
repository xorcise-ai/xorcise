import { Loader2 } from "lucide-react";
import type { ConnectPhase } from "./queries";

/** Green/red presence dot shared by the settings cards. */
export function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={"inline-block size-2 rounded-full " + (ok ? "bg-ok" : "bg-err")}
      aria-hidden
    />
  );
}

/** The single inline save→verify status line: exactly one message at a time
 *  (Saving… → Verifying connection… → Connected / failure), replacing the old
 *  cluster of independent Saved. / Key works. / error spans. */
export function ConnectionStatus({
  phase,
  message,
}: {
  phase: ConnectPhase;
  message?: string | null;
}) {
  if (phase === "idle") return null;
  if (phase === "saving" || phase === "testing") {
    return (
      <span role="status" className="flex items-center gap-1.5 text-dense text-text-secondary">
        <Loader2 className="size-3.5 motion-safe:animate-spin text-primary" aria-hidden />
        {phase === "saving" ? "Saving…" : "Verifying connection…"}
      </span>
    );
  }
  if (phase === "connected") {
    return (
      <span role="status" className="flex items-center gap-1.5 text-dense text-ok">
        <Dot ok />
        Connected
      </span>
    );
  }
  if (phase === "save_error") {
    return (
      <span role="status" className="text-dense text-err">
        Couldn&apos;t save.
      </span>
    );
  }
  if (phase === "not_configured") {
    return (
      <span role="status" className="text-dense text-text-tertiary">
        Not configured
      </span>
    );
  }
  // test_error — surface the provider's message when it sent one.
  return (
    <span role="status" className="flex items-center gap-1.5 text-dense text-err">
      <Dot ok={false} />
      {message ?? "Connection failed."}
    </span>
  );
}
