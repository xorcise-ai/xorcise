import { describe, it, expect, afterEach } from "vitest";
import { apiBaseUrl } from "./runtime-config";

afterEach(() => {
  delete window.__XORCISE_API_BASE__;
});

describe("apiBaseUrl", () => {
  it("defaults to <origin>/api", () => {
    // jsdom origin in this harness is http://localhost:3000
    expect(apiBaseUrl()).toBe("http://localhost:3000/api");
  });

  it("honors a runtime override on window", () => {
    window.__XORCISE_API_BASE__ = "http://10.0.0.5:3001/api";
    expect(apiBaseUrl()).toBe("http://10.0.0.5:3001/api");
  });

  it("strips a trailing slash from the override", () => {
    window.__XORCISE_API_BASE__ = "http://10.0.0.5:3001/api/";
    expect(apiBaseUrl()).toBe("http://10.0.0.5:3001/api");
  });
});
