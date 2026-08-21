import { describe, it, expect } from "vitest";

import { missionFixture } from "@/test/fixtures";

import { platformTags } from "./mission-card";

describe("platformTags", () => {
  it("names the platforms of a mission that actually ships an image", () => {
    const tags = platformTags(
      missionFixture({
        type: "lab",
        image: "registry/mis-x:1",
        platforms: ["linux/amd64", "linux/arm64"],
        installed: true,
        platform: "linux/arm64",
      }),
    );
    expect(tags.map((t) => t.platform)).toEqual(["linux/amd64", "linux/arm64"]);
    expect(tags.find((t) => t.platform === "linux/arm64")?.installed).toBe(true);
  });

  it("claims no architecture for a static mission", () => {
    // The catalog still sends platforms: ["linux/amd64"] for attachment-only missions, which
    // would otherwise paint an AMD64 badge on something that never runs a container.
    const tags = platformTags(
      missionFixture({ type: "static", image: null, platforms: ["linux/amd64"] }),
    );
    expect(tags).toEqual([]);
  });

  it("claims no architecture when there is no image, whatever the type says", () => {
    const tags = platformTags(
      missionFixture({ type: "lab", image: null, platforms: ["linux/amd64"] }),
    );
    expect(tags).toEqual([]);
  });

  it("still tags an uninstalled lab mission — image tracks type, not install state", () => {
    const tags = platformTags(
      missionFixture({
        type: "lab",
        image: "registry/mis-y:1",
        installed: false,
        platforms: ["linux/amd64", "linux/arm64"],
      }),
    );
    expect(tags.map((t) => t.platform)).toEqual(["linux/amd64", "linux/arm64"]);
    expect(tags.every((t) => !t.installed)).toBe(true);
  });
});
