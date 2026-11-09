import { describe, expect, it } from "vitest";

import { resolveStageLabel } from "@/components/workspace/video-gen/vimax-stage-indicator";

// VimaxStageIndicator is presentational; per project convention we unit-test the
// pure label-resolution logic (no JSX in .test.ts). The component renders this
// label + meta key/values under the coarse progress bar.

describe("resolveStageLabel", () => {
  it("returns empty when there is no stage (non-vimax / pre-stage tasks render nothing)", () => {
    expect(resolveStageLabel(undefined, undefined)).toBe("");
  });

  it("returns the Chinese label for a known stage", () => {
    expect(
      resolveStageLabel("character_portraits", "Generating portraits"),
    ).toBe("生成角色肖像");
    expect(resolveStageLabel("design_storyboard", undefined)).toBe("设计分镜");
    expect(resolveStageLabel("decompose_shots", undefined)).toBe("拆解镜头");
  });

  it("falls back to the ViMax message for an unmapped stage", () => {
    expect(resolveStageLabel("some_new_stage", "Doing X")).toBe("Doing X");
  });

  it("falls back to the raw stage when neither label nor message exist", () => {
    expect(resolveStageLabel("mystery_stage", undefined)).toBe("mystery_stage");
  });
});
