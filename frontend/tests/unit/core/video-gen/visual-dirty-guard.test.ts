import { describe, expect, test } from "vitest";

import { supportsVisualDirtyGuard } from "@/core/video-gen/visual-dirty-guard";

describe("supportsVisualDirtyGuard（E5 付费前守卫的类型门控）", () => {
  test.each(["ai_image", "ai_video", "ai_visual"])(
    "%s 启用未保存编辑守卫",
    (videoType) => {
      expect(supportsVisualDirtyGuard(videoType)).toBe(true);
    },
  );

  test.each([
    "vimax",
    "remotion_course",
    "remotion",
    "ai_visual_legacy",
    "",
    undefined,
  ])("%s 不启用（ViMax / remotion 保持原按钮规则）", (videoType) => {
    expect(supportsVisualDirtyGuard(videoType)).toBe(false);
  });
});
