// Resolve the server's /api base at RUNTIME — never baked in at build time, so
// the same static export works served from the server at /ui (default), in dev,
// or pointed at a remote server (location transparency).
//
// Override precedence:
//   1. window.__XORCISE_API_BASE__  (injected at runtime, e.g. dev / remote)
//   2. <origin>/api                  (the server serves the UI at /ui on the same origin)

declare global {
  interface Window {
    __XORCISE_API_BASE__?: string;
  }
}

export function apiBaseUrl(): string {
  if (typeof window !== "undefined" && window.__XORCISE_API_BASE__) {
    return stripTrailingSlash(window.__XORCISE_API_BASE__);
  }
  if (typeof window !== "undefined") {
    return `${window.location.origin}/api`;
  }
  return "/api";
}

function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}
