import { describe, it, expect, beforeEach } from "vitest";
import { useUiStore } from "./ui";

beforeEach(() => {
  useUiStore.setState({ sidebarCollapsed: false });
});

describe("ui store", () => {
  it("defaults the sidebar to expanded", () => {
    expect(useUiStore.getState().sidebarCollapsed).toBe(false);
  });

  it("toggles sidebar collapse", () => {
    useUiStore.getState().toggleSidebar();
    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
    useUiStore.getState().toggleSidebar();
    expect(useUiStore.getState().sidebarCollapsed).toBe(false);
  });

  it("sets sidebar collapse explicitly", () => {
    useUiStore.getState().setSidebarCollapsed(true);
    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
  });
});
