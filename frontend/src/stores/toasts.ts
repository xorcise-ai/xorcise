import { create } from "zustand";

// Ephemeral notification state (NOT persisted — a toast is only meaningful in
// the session it fired in). Server state stays in TanStack Query.

export type ToastTone = "ok" | "err" | "warn" | "info";

export interface Toast {
  id: string;
  title: string;
  body?: string;
  tone: ToastTone;
  /** Optional action link (internal route) rendered under the title. */
  href?: string;
  hrefLabel?: string;
  /** Set when the toast is animating out; the toast is removed after TOAST_EXIT_MS. */
  leaving?: boolean;
}

/** How long a toast stays up before auto-dismissing. */
export const TOAST_AUTO_DISMISS_MS = 7000;

/** How long the exit animation gets before the toast is removed. */
export const TOAST_EXIT_MS = 200;

// Auto-dismiss timers live outside the store so dismiss() can cancel a pending
// timer without keeping non-serializable handles in state.
const timers = new Map<string, ReturnType<typeof setTimeout>>();

let seq = 0;

interface ToastState {
  toasts: Toast[];
  push: (toast: Omit<Toast, "id">) => string;
  dismiss: (id: string) => void;
}

export const useToastStore = create<ToastState>()((set, get) => ({
  toasts: [],
  push: (toast) => {
    const id = `toast-${++seq}`;
    set((s) => ({ toasts: [...s.toasts, { ...toast, id }] }));
    timers.set(
      id,
      setTimeout(() => get().dismiss(id), TOAST_AUTO_DISMISS_MS),
    );
    return id;
  },
  dismiss: (id) => {
    const toast = get().toasts.find((t) => t.id === id);
    // Unknown or already exiting: leave the pending removal timer alone —
    // clearing it here would strand a leaving toast forever.
    if (!toast || toast.leaving) return;
    const timer = timers.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      timers.delete(id);
    }
    // Two-phase: mark leaving so the host can play the exit animation, then
    // actually remove once it has had time to run.
    set((s) => ({
      toasts: s.toasts.map((t) => (t.id === id ? { ...t, leaving: true } : t)),
    }));
    timers.set(
      id,
      setTimeout(() => {
        timers.delete(id);
        set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
      }, TOAST_EXIT_MS),
    );
  },
}));
