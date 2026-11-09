import { describe, expect, it } from "vitest";

import { shouldShowStaleStyleBanner } from "@/components/workspace/video-gen/course-project-detail";

// X11（2026-08-27 方案 B）：shouldShowSheetGuidance 随「角色参考」失实区块退役——
// character_reference 文本无主链路消费方（后端按图片路径消费），区块整体删除。

// U5: 风格预设切换 + 已有角色图 → 提示旧角色图基于旧风格，建议重新生成。
// 项目约定：.test.ts 只测纯函数（无 JSX）。

describe("shouldShowStaleStyleBanner (U5)", () => {
  it("shows when preset changed and sheets exist", () => {
    expect(shouldShowStaleStyleBanner("watercolor", "3D render", 1)).toBe(true);
  });

  it("hides when no sheets", () => {
    expect(shouldShowStaleStyleBanner("watercolor", "3D render", 0)).toBe(
      false,
    );
  });

  it("hides when preset unchanged", () => {
    expect(shouldShowStaleStyleBanner("watercolor", "watercolor", 2)).toBe(
      false,
    );
  });

  it("hides when both empty (unset)", () => {
    expect(shouldShowStaleStyleBanner("", "", 1)).toBe(false);
  });

  // R6 (Finding 7): 首屏 originalPreset=持久化值（非空），currentPreset=""（useState 初始）→
  // 守卫须挡住，避免一帧误报横幅。
  it("hides on first render (currentPreset not synced yet)", () => {
    expect(shouldShowStaleStyleBanner("watercolor", "", 1)).toBe(false);
  });

  // R6: 原无预设→选了新预设，旧 sheet 无风格 → 应提示重新生成（勿被 currentPreset!="" 守卫误杀）。
  it("shows when switching from no preset to a new preset with old sheets", () => {
    expect(shouldShowStaleStyleBanner("", "watercolor", 1)).toBe(true);
  });
});

// Task 1 (2026-08-01): 存量英文字面量在「旧风格横幅」判定里不得误报。
//
// 风格 value 从英文字面量（'3D render'）改成语言无关 key（education_3d）后，
// 同一个风格在服务端与 state 里会是**两种写法**。若判定不先归一，
// 打开老项目会立刻误报「风格已变，建议重新生成角色图」—— 用户没改任何东西。
describe("shouldShowStaleStyleBanner —— 存量值归一（Task 1）", () => {
  it("同一风格的旧写法与新 key 不应误报为已变更", () => {
    // 服务端存 '3D render'，UI 归一后 state 是 education_3d —— 语义相同
    expect(shouldShowStaleStyleBanner("3D render", "education_3d", 2)).toBe(
      false,
    );
    expect(
      shouldShowStaleStyleBanner("flat illustration", "education_flat", 1),
    ).toBe(false);
  });

  it("真的换了风格仍要提示（不能为消除误报而一并压掉真信号）", () => {
    expect(shouldShowStaleStyleBanner("3D render", "photorealistic", 1)).toBe(
      true,
    );
    expect(shouldShowStaleStyleBanner("watercolor", "education_3d", 1)).toBe(
      true,
    );
  });
});
