import { describe, expect, it } from "vitest";

import {
  hasVimaxShotManifest,
  resolveStoryboardCounts,
} from "@/core/video-gen/storyboard-counts";

describe("resolveStoryboardCounts", () => {
  it("prefers server summary frame_count over storyboard frames", () => {
    const got = resolveStoryboardCounts(
      { frame_count: 12, shot_count: 8, shots_ready: true },
      { frames: new Array(7).fill({}) },
    );
    expect(got.frameCount).toBe(12);
  });

  it("falls back to storyboard frames length when summary missing", () => {
    const got = resolveStoryboardCounts({}, { frames: new Array(5).fill({}) });
    expect(got.frameCount).toBe(5);
  });

  it("keeps numeric shot_count available independent of task status", () => {
    const got = resolveStoryboardCounts(
      { frame_count: 12, shot_count: 0 },
      { frames: new Array(12).fill({}) },
    );
    expect(got.shotCount).toBeNull();
  });

  it("shows shot count when ready", () => {
    const got = resolveStoryboardCounts(
      { frame_count: 12, shot_count: 8, shots_ready: true },
      { frames: new Array(12).fill({}) },
    );
    expect(got.shotCount).toBe(8);
  });
});

describe("hasVimaxShotManifest", () => {
  it("treats non-empty manifests as redoable regardless of task status", () => {
    expect(hasVimaxShotManifest({ "0": { prompt: "p" } })).toBe(true);
    expect(hasVimaxShotManifest({})).toBe(false);
    expect(hasVimaxShotManifest(undefined)).toBe(false);
  });
});
