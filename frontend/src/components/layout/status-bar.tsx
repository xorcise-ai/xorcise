"use client";

import { cn } from "@/components/ui/cn";
import { useSystem } from "@/features/settings/queries";
import {
  groupByRole,
  roleTooltip,
  type ModuleState,
  type RoleGroup,
} from "@/features/settings/module-groups";

const STATE_DOT: Record<ModuleState, string> = {
  ok: "bg-ok",
  down: "bg-err",
  // Absent is NOT a failure: a module this host was never meant to run is dimmed, not red.
  not_deployed: "bg-text-tertiary/40",
};

/**
 * The always-visible footer: is the server there, and is each of its service roles reachable.
 *
 * Per-role dots exist because XORCISE is designed to run its roles on SEPARATE MACHINES
 * (`xorcise serve --role <key>`). Today they collapse into one process, so all four are local
 * — but the operator still needs one place that answers "is every part of my deployment up?",
 * and this bar keeps working unchanged when a role really does move to another host.
 *
 * The role dots are only rendered once the system probe has answered. Rendering a placeholder
 * dot before then would state a verdict nothing has verified.
 */
export function StatusBar({ healthy }: { healthy: boolean }) {
  const system = useSystem();
  const groups = groupByRole(system.data?.planes ?? []);

  return (
    <footer className="flex h-8 shrink-0 items-center gap-3 border-t border-border bg-chrome px-6 text-label uppercase text-text-tertiary">
      {/* No aggregate "xorcise.core connected" line. Once every role reports for itself, a
          single global dot restated what the four dots beside it already said, in less detail.
          Unreachable still gets its own explicit line, because THEN the role dots know nothing
          and an empty bar would read as "fine". */}
      {healthy ? (
        <ul className="flex items-center gap-3">
          {groups.map((group) => (
            <RoleDot key={group.role} group={group} />
          ))}
        </ul>
      ) : (
        <span className="flex items-center gap-2 text-err">
          <span className="size-2 rounded-full bg-err" aria-hidden />
          <span>xorcise.core unreachable</span>
        </span>
      )}
    </footer>
  );
}

function RoleDot({ group }: { group: RoleGroup }) {
  const tooltip = roleTooltip(group);
  return (
    <li
      // `title` gives the native hover tooltip; aria-label carries the same sentence to a
      // screen reader, which never sees a title on a non-interactive element.
      title={tooltip}
      aria-label={tooltip}
      className="flex cursor-default items-center gap-1.5"
    >
      <span className={cn("size-2 rounded-full", STATE_DOT[group.state])} aria-hidden />
      <span className={cn(group.state === "not_deployed" && "opacity-60")}>{group.label}</span>
    </li>
  );
}
