/**
 * Task 6（2026-08-03）: remotion 模式不显示风格下拉。
 *
 * 风格是生成提示词前缀概念（后端 style_presets.py 注入中文 prefix），
 * remotion 无生成步骤（orchestration/storyboard_planner.py 对 remotion 直接 return），
 * 显示它等于承诺一个静默失效的旋钮。
 */
import { describe, expect, it } from "vitest";

import { shouldShowStyleSelector } from "@/components/workspace/video-gen/style-visibility";

describe("shouldShowStyleSelector", () => {
  it("shows for ai_image", () => {
    expect(shouldShowStyleSelector("ai_image")).toBe(true);
  });

  it("shows for ai_video", () => {
    expect(shouldShowStyleSelector("ai_video")).toBe(true);
  });

  it("shows for vimax", () => {
    expect(shouldShowStyleSelector("vimax")).toBe(true);
  });

  it("hides for remotion", () => {
    expect(shouldShowStyleSelector("remotion")).toBe(false);
  });
});
