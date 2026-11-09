import { describe, expect, it } from "vitest";

import {
  courseProjectCoverUrls,
  courseProjectFallbackColor,
  courseProjectStyleLabel,
} from "@/components/workspace/video-gen/course-project-page";
import { stylePreviewImage } from "@/core/video-gen/style-presets";
import type { CourseProject } from "@/core/video-gen/types";

function project(overrides: Partial<CourseProject> = {}): CourseProject {
  return {
    id: "p1",
    user_id: "u1",
    title: "项目",
    visual_style_preset: "precision_eng",
    color_palette: { primary: "#123456" },
    style_anchor_image: "/private/server/anchor.png",
    created_at: "2026-11-01T00:00:00Z",
    updated_at: null,
    ...overrides,
  };
}

describe("course project cover", () => {
  it("prioritizes anchor then style preview then color", () => {
    expect(courseProjectCoverUrls(project())).toEqual([
      "/api/video/projects/p1/style-anchor-image",
      stylePreviewImage("precision_eng"),
    ]);
    expect(
      courseProjectCoverUrls(project({ style_anchor_image: null })),
    ).toEqual([stylePreviewImage("precision_eng")]);
    expect(
      courseProjectCoverUrls(
        project({ style_anchor_image: null, visual_style_preset: "unknown" }),
      ),
    ).toEqual([]);
    expect(courseProjectFallbackColor(project())).toBe("#123456");
    expect(courseProjectFallbackColor(project({ color_palette: null }))).toBe(
      "#eef2ff",
    );
    expect(
      courseProjectFallbackColor(project({ color_palette: { primary: " " } })),
    ).toBe("#eef2ff");
  });

  it("never exposes local anchor path and labels legacy/unknown styles honestly", () => {
    expect(courseProjectCoverUrls(project()).join(" ")).not.toContain(
      "/private/server/anchor.png",
    );
    expect(
      courseProjectStyleLabel(project({ visual_style_preset: "3D render" })),
    ).toBe("3D 渲染");
    expect(
      courseProjectStyleLabel(
        project({ visual_style_preset: "unknown-custom" }),
      ),
    ).toBe("unknown-custom");
    expect(
      courseProjectStyleLabel(project({ visual_style_preset: null })),
    ).toBe("未设置风格");
  });
});
