"use client";

import { useEffect, useState } from "react";
import { ArrowUp, File, Folder } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/skeleton";
import { useFsList } from "./queries";

/**
 * A read-only picker over the server host's filesystem. The browser can't read a
 * path, so the server lists directories (GET /api/fs/list) and the operator
 * navigates + selects; the chosen absolute path is returned via `onSelect`.
 * Local-only app, so browsing the local tree is by design.
 *
 * A single editable path bar is the source of truth: it shows the current
 * location, accepts a typed/pasted path (Enter or Go to browse it), and is
 * exactly what gets confirmed. `mode="directory"` confirms the path in the bar
 * (the bundle folder); `mode="file"` confirms a picked/typed file.
 */
export function FileBrowser({
  open,
  onClose,
  onSelect,
  initialPath = null,
  title = "Pick a file",
  mode = "file",
}: {
  open: boolean;
  onClose: () => void;
  onSelect: (path: string) => void;
  initialPath?: string | null;
  title?: string;
  mode?: "file" | "directory";
}) {
  // `path` is the single editable address bar — the source of truth for Go + confirm.
  // `browse` is the directory currently listed (drives the fetch); it trails navigation.
  // `dirty` marks the bar as user-edited so an in-flight listing never clobbers their typing.
  const [path, setPath] = useState(initialPath ?? "");
  const [browse, setBrowse] = useState<string | null>(initialPath);
  const [pickedFile, setPickedFile] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const listing = useFsList(browse, open); // don't list until the picker is open
  const dirMode = mode === "directory";

  // Re-anchor to the requested start dir each time the picker opens.
  useEffect(() => {
    if (open) {
      setPath(initialPath ?? "");
      setBrowse(initialPath);
      setPickedFile(null);
      setDirty(false);
    }
  }, [open, initialPath]);

  // After a navigation resolves, fill the bar with the absolute directory (so `~`/relative/blank
  // inputs show their resolved form) — but never while the user is mid-edit (`dirty`), so a typed
  // path that's still being confirmed isn't overwritten by a stale in-flight listing.
  const resolved = listing.data?.path;
  useEffect(() => {
    if (resolved && !dirty) setPath(resolved);
  }, [resolved, dirty]);

  function navigate(p: string | null) {
    const t = p && p.trim() ? p.trim() : null;
    setDirty(false);
    setPickedFile(null);
    setBrowse(t);
    setPath(t ?? ""); // the resolve effect refines this to the absolute path once listed
  }

  // File mode gates confirm on an actual file (a clicked entry, or a typed path that isn't just
  // the directory we're listing). Directory mode confirms whatever is in the bar.
  const typedFile =
    !dirMode && !!path.trim() && path.trim() !== (listing.data?.path ?? null);
  const chosen = dirMode ? path.trim() : (pickedFile ?? (typedFile ? path.trim() : ""));
  const canConfirm = !!chosen;

  function confirm() {
    if (!chosen) return;
    onSelect(chosen);
    onClose();
  }

  return (
    <Dialog open={open} onClose={onClose} title={title} size="lg">
      <div className="space-y-3">
        {/* Single editable address bar — up, the path, and Go to browse the typed path. */}
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!listing.data?.parent}
            onClick={() => navigate(listing.data?.parent ?? null)}
            aria-label="Up one directory"
          >
            <ArrowUp className="size-4" />
          </Button>
          <input
            type="text"
            aria-label="Path"
            value={path}
            onChange={(e) => {
              setPath(e.target.value);
              setDirty(true); // user is editing — don't let a listing overwrite it
              setPickedFile(null); // typing overrides a previously-clicked file
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                navigate(path);
              }
            }}
            placeholder={
              dirMode
                ? "Type or paste a directory path…"
                : "Type or paste a file path…"
            }
            className="flex-1 rounded-md border border-border bg-background px-2 py-1.5 font-mono text-dense text-foreground placeholder:text-text-tertiary"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!path.trim()}
            onClick={() => navigate(path)}
          >
            Go
          </Button>
        </div>

        {/* Entries */}
        <div className="h-72 overflow-y-auto rounded-lg border border-border bg-background">
          {listing.isLoading && (
            <div role="status" aria-label="Listing…" className="p-4">
              <SkeletonRows count={6} />
            </div>
          )}
          {listing.isError && (
            <p className="p-4 text-body text-err">
              Couldn’t list this directory (it may not exist or be readable).
            </p>
          )}
          {listing.data && listing.data.entries.length === 0 && (
            <p className="p-4 text-body text-text-tertiary">Empty directory.</p>
          )}
          {listing.data && listing.data.entries.length > 0 && (
            <ul className="divide-y divide-border">
              {listing.data.entries.map((e) => {
                const isSelected = !dirMode && !e.is_dir && pickedFile === e.path;
                // In directory mode, files are context only (greyed, not selectable).
                const inert = dirMode && !e.is_dir;
                return (
                  <li key={e.path}>
                    <button
                      type="button"
                      disabled={inert}
                      onClick={() => {
                        if (e.is_dir) navigate(e.path);
                        else if (!dirMode) {
                          setPickedFile(e.path);
                          setPath(e.path);
                          setDirty(true); // a picked file must survive an in-flight listing
                        }
                      }}
                      onDoubleClick={() => {
                        if (!dirMode && !e.is_dir) {
                          onSelect(e.path);
                          onClose();
                        }
                      }}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-left text-dense transition-colors ${
                        inert
                          ? "cursor-default text-text-tertiary/60"
                          : "hover:bg-card"
                      } ${isSelected ? "bg-card text-heading" : !inert ? "text-text-secondary" : ""}`}
                    >
                      {e.is_dir ? (
                        <Folder className="size-4 shrink-0 text-primary" />
                      ) : (
                        <File className="size-4 shrink-0 text-text-tertiary" />
                      )}
                      <span className="truncate">{e.name}</span>
                      {e.is_dir && (
                        <span className="ml-auto text-text-tertiary">/</span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2">
          <span className="min-w-0 flex-1 truncate text-caption text-text-tertiary">
            {dirMode
              ? "Type or navigate to the bundle folder, then confirm."
              : "Click a file (double-click to confirm) or type a path."}
          </span>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!canConfirm}
              onClick={confirm}
            >
              {dirMode ? "Use this folder" : "Use this file"}
            </Button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
