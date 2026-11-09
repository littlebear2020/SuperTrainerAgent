/**
 * Task 1 存量数据护栏：老项目存的旧值必须仍能在下拉里**正确显示并回存**。
 *
 * 真实风险（实测存量数据）：
 *   cp_f3d9cc794a25.visual_style_preset = '3D render'
 *   cp_d11fb1691f4f.visual_style_preset = 'flat illustration'
 *   cp_a9bd1420219b.visual_style_preset = 'watercolor'
 *
 * course-project-detail.tsx 用原生 <select value={stylePreset}> 渲染，
 * 原生 select 在 value 找不到匹配 <option> 时会**静默回落到第一项**（「不设置」）——
 * 用户打开老项目会看到风格变成「不设置」，一保存就把风格**真的清空**。
 * 这属于静默数据丢失，比显示错误严重。
 *
 * 2026-08-19 项目级收敛（两份反馈 T1 偏离项 1）后，承载从「静态常量里塞
 * watercolor/education_flat 两条」改为与任务级同构的**动态承载**：
 * projectStyleOptions(stored) 在基座（不设置 + 推荐 7）外，按存量值追加承载项。
 * 本文件锁「任意存量值都能解析到一个真实存在的 option」。
 */

import { describe, expect, it } from "vitest";

import {
  optionValueForStored,
  projectStyleOptions,
  resolveStyleValue,
} from "@/core/video-gen/style-presets";

/** 实测存量项目里出现过的 visual_style_preset 值 */
const REAL_LEGACY_VALUES = [
  "3D render",
  "flat illustration",
  "watercolor",
  "minimalist",
  "photorealistic",
  "cinematic",
];

describe("Task 1: 存量项目的风格值必须能在下拉里正确显示", () => {
  it("每个存量值都能解析到一个真实存在的 option（不静默回落「不设置」）", () => {
    for (const stored of REAL_LEGACY_VALUES) {
      const shown = optionValueForStored(stored);
      expect(shown, `存量值 ${stored} 不得解析为「不设置」`).not.toBe("");
      expect(
        projectStyleOptions(shown).some((o) => o.value === shown),
        `存量值 ${stored} 解析为 ${shown}，但下拉里没有这个 option → 会静默显示为「不设置」并在保存时清空风格`,
      ).toBe(true);
    }
  });

  it("'3D render' 归一为 education_3d，以承载项「3D 渲染」显示（不是「不设置」）", () => {
    expect(optionValueForStored("3D render")).toBe("education_3d");
    const opt = projectStyleOptions("education_3d").find(
      (o) => o.value === "education_3d",
    );
    expect(opt?.label).toBe("3D 渲染");
  });

  it("watercolor 从两级 UI 移除，但项目级下拉必须仍能承载显示（否则老项目风格被清空）", () => {
    const shown = optionValueForStored("watercolor");
    expect(shown).toBe("watercolor");
    expect(
      projectStyleOptions(shown).some((o) => o.value === "watercolor"),
      "项目级下拉需含 watercolor 承载项以承载存量数据",
    ).toBe(true);
  });

  it("空值 / 未设置仍是「不设置」", () => {
    expect(optionValueForStored("")).toBe("");
    expect(optionValueForStored(null)).toBe("");
    expect(optionValueForStored(undefined)).toBe("");
  });

  it("未知值不猜：解析不到就回落「不设置」而非乱指一个风格", () => {
    // 与 resolveStyleValue 的「原样返回」不同：用于**渲染**时，未知值没有对应
    // option，只能显示「不设置」；关键是不得错指成某个具体风格。
    expect(resolveStyleValue("某种没见过的风格")).toBe("某种没见过的风格");
    expect(optionValueForStored("某种没见过的风格")).toBe("");
    // 动态承载也不得为未知值凭空造选项
    expect(
      projectStyleOptions("某种没见过的风格").some(
        (o) => o.value === "某种没见过的风格",
      ),
    ).toBe(false);
  });
});
