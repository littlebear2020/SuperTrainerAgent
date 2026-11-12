import { describe, expect, it } from "vitest";

import {
  buildVideoAssetsHref,
  isWorkCapability,
  parseVideoAssetsRoute,
  WORK_CAPABILITY_IDS,
} from "@/core/workspace/video-assets-route";

describe("video-assets-route", () => {
  it("defaults the bare path to works and course-video", () => {
    expect(parseVideoAssetsRoute("")).toEqual({
      tab: "works",
      capability: "course-video",
    });
    expect(
      buildVideoAssetsHref({ tab: "works", capability: "course-video" }),
    ).toBe("/workspace/video-gen");
  });

  it.each([...WORK_CAPABILITY_IDS])(
    "accepts the %s capability guard",
    (capability) => {
      expect(isWorkCapability(capability)).toBe(true);
      expect(
        parseVideoAssetsRoute(`tab=works&capability=${capability}`),
      ).toEqual({
        tab: "works",
        capability,
      });
    },
  );

  it("parses assets without a capability", () => {
    expect(parseVideoAssetsRoute("tab=assets&capability=ai_video")).toEqual({
      tab: "assets",
    });
    expect(buildVideoAssetsHref({ tab: "assets" })).toBe(
      "/workspace/video-gen?tab=assets",
    );
  });

  it("falls back independently for invalid tab and capability values", () => {
    expect(parseVideoAssetsRoute("tab=invalid&capability=ai_image")).toEqual({
      tab: "works",
      capability: "ai_image",
    });
    expect(parseVideoAssetsRoute("tab=works&capability=invalid")).toEqual({
      tab: "works",
      capability: "course-video",
    });
    expect(isWorkCapability("invalid")).toBe(false);
  });

  it("round-trips a non-default works capability", () => {
    expect(
      buildVideoAssetsHref({ tab: "works", capability: "ppt-master" }),
    ).toBe("/workspace/video-gen?tab=works&capability=ppt-master");
  });

  it("preserves unrelated params while canonicalizing only tab and capability", () => {
    expect(
      buildVideoAssetsHref(
        { tab: "assets" },
        "foo=1&utm_source=review&capability=ai_video&tab=works",
      ),
    ).toBe("/workspace/video-gen?foo=1&utm_source=review&tab=assets");
    expect(
      buildVideoAssetsHref(
        { tab: "works", capability: "course-video" },
        "foo=1&tab=works&capability=invalid&utm_campaign=second",
      ),
    ).toBe("/workspace/video-gen?foo=1&utm_campaign=second");
    expect(
      buildVideoAssetsHref(
        { tab: "works", capability: "ai_image" },
        "foo=1&capability=invalid&utm_source=review",
      ),
    ).toBe(
      "/workspace/video-gen?foo=1&utm_source=review&tab=works&capability=ai_image",
    );
  });
});
