import { create } from "zustand";
import { persist } from "zustand/middleware";

/** Default seconds of OTel silence on a live run before the agent is flagged inactive. */
export const DEFAULT_STALL_THRESHOLD_SECONDS = 180;

// UI-only state (NOT server state — that lives in TanStack Query). Persisted to
// localStorage so the operator's shell preferences survive reloads.
interface UiState {
  sidebarCollapsed: boolean;
  /** Seconds without a fresh OTel export before a live run warns "agent inactive". */
  stallThresholdSeconds: number;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setStallThresholdSeconds: (seconds: number) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      stallThresholdSeconds: DEFAULT_STALL_THRESHOLD_SECONDS,
      toggleSidebar: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      setStallThresholdSeconds: (seconds) =>
        set({ stallThresholdSeconds: seconds }),
    }),
    { name: "xorcise-ui" },
  ),
);
