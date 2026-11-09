/**
 * Task 1（2026-08-01 反馈 4）：风格预设专业化。
 *
 * 修复前的两个问题：
 * 1. 任务级只有 4 个泛化词（默认/专业/轻松/电影感），描述的是**语气**而非**画风**，
 *    对生图模型没有指导力 —— 用户说「不够专业」。
 * 2. 任务级 4 个词与项目级 5 个视觉预设（扁平插画/水彩/3D渲染/极简/写实）
 *    **两套词汇割裂**，且项目级那份在 course-project-dialog.tsx 与
 *    course-project-detail.tsx 里**各抄了一份**。
 *
 * 本文件锁「单一来源 + 语言无关 key」这两条不变量。
 *
 * ⚠️ **key 必须语言无关**（如 education_3d），不能是英文字面量（如 "3D render"）。
 * 实测存量项目把 value 存成英文字面量后，course_models.style_prefix 会把它
 * **原样拼进生图 prompt**（真实任务 vg_d0779ddd81f8 的 storyboard.json 佐证：
 * 中文正文被英文前缀夹在中间）。UI 的 value 绝不能直接进 prompt，
 * 必须由后端查表翻译成中文 prefix。
 */

import { describe, expect, it } from "vitest";

import {
  BACKEND_ONLY_STYLE_KEYS,
  BACKEND_ONLY_STYLE_PRESETS,
  LEGACY_STYLE_VALUES,
  optionValueForStored,
  PROJECT_STYLE_OPTIONS,
  projectStyleOptions,
  resolveStyleValue,
  STYLE_PRESETS,
  STYLE_PRESET_GROUPS,
} from "@/core/video-gen/style-presets";

describe("Task 1: 风格预设单一来源", () => {
  it("导出 8 项预设（default + 推荐 7；旧 7 项 2026-08-19 项目级收敛后移入 BACKEND_ONLY）", () => {
    // 项目级下拉收敛（两份反馈 T1 偏离项 1）：旧 7 项在任务级（T1）与项目级
    // 均不再给新选，成为「后端保留、两级 UI 不给」的存量承载 key。
    expect(STYLE_PRESETS).toHaveLength(8);
  });

  it("每项都有 value / label，且 value 唯一", () => {
    for (const p of STYLE_PRESETS) {
      expect(p.value, "value 不得为空").toBeTruthy();
      expect(p.label, `${p.value} 应有中文 label`).toBeTruthy();
    }
    const values = STYLE_PRESETS.map((p) => p.value);
    expect(new Set(values).size, "value 必须唯一").toBe(values.length);
  });

  it("含 default 项（不设定风格），且它不属于任何分组", () => {
    const d = STYLE_PRESETS.find((p) => p.value === "default");
    expect(d, "应含 default 项").toBeTruthy();
    expect(d!.group, "default 不应归入任何分组").toBeUndefined();
  });

  it("分组只剩「推荐」一种（2026-08-19 项目级收敛：职场/实景/叙事 旧组随旧 7 项退役）", () => {
    const groups: string[] = [
      ...new Set(
        STYLE_PRESETS.map((p) => p.group).filter(
          (g): g is NonNullable<typeof g> => g !== undefined,
        ),
      ),
    ];
    expect(groups).toEqual(["推荐"]);
    // 常量表与实际用到的分组一致
    expect([...STYLE_PRESET_GROUPS]).toEqual(groups);
  });

  it("清单与拍板结果逐项一致（含分组归属）", () => {
    expect(
      STYLE_PRESETS.map((p) => [p.value, p.label, p.group ?? null]),
    ).toEqual([
      ["default", "默认（不设定）", null],
      ["precision_eng", "精密工程", "推荐"],
      ["paper_collage", "纸艺拼贴", "推荐"],
      ["papercraft_theater", "纸艺剧场", "推荐"],
      ["character_3d", "3D 角色", "推荐"],
      ["corporate_live", "商务实景", "推荐"],
      ["isometric_info", "等距信息图", "推荐"],
      ["whiteboard", "白板手绘", "推荐"],
    ]);
  });

  it("「水彩手绘」已从 UI 下拉移除（用户决策：删该项，原「艺术」组整组取消）", () => {
    expect(STYLE_PRESETS.some((p) => p.value === "watercolor")).toBe(false);
    expect(STYLE_PRESETS.some((p) => p.label.includes("水彩"))).toBe(false);
  });

  it("value 必须语言无关：不得含空格或大写（防英文字面量直接进 prompt）", () => {
    for (const p of STYLE_PRESETS) {
      expect(p.value, `${p.value} 不应含空格（英文字面量特征）`).not.toMatch(
        /\s/,
      );
      expect(p.value, `${p.value} 应为小写 snake_case`).toMatch(
        /^[a-z][a-z0-9_]*$/,
      );
    }
  });

  it("存量旧值保留在 LEGACY_STYLE_VALUES（只读兼容，不能 422 也不能退化）", () => {
    // watercolor：实测存量项目 cp_a9bd1420219b 就存着它
    // professional/casual：任务级旧下拉值，存量草稿 payload 仍在用
    // 三个英文字面量：实测存量项目 cp_f3d9cc794a25 / cp_d11fb1691f4f 存着
    for (const legacy of [
      "watercolor",
      "professional",
      "casual",
      "3D render",
      "flat illustration",
    ]) {
      expect(
        LEGACY_STYLE_VALUES,
        `旧值 ${legacy} 必须可识别，否则老项目/老草稿会退化成无风格前缀`,
      ).toHaveProperty(legacy);
    }
  });

  it("LEGACY_STYLE_VALUES 的每个映射目标都是后端认识的 key（别名不得指向空气）", () => {
    // 目标可以是 UI 的 9 项，也可以是后端保留项（watercolor：UI 已删但存量项目仍用）
    const valid = new Set([
      ...STYLE_PRESETS.map((p) => p.value),
      ...BACKEND_ONLY_STYLE_KEYS,
    ]);
    for (const [legacy, target] of Object.entries(LEGACY_STYLE_VALUES)) {
      expect(
        valid.has(target),
        `别名 ${legacy} -> ${target}，但 ${target} 不是后端认识的 key`,
      ).toBe(true);
    }
  });

  it("watercolor 属「后端保留、UI 不给」——两条约束同时成立", () => {
    expect(BACKEND_ONLY_STYLE_KEYS).toContain("watercolor");
    // UI 不给新选
    expect(STYLE_PRESETS.some((p) => p.value === "watercolor")).toBe(false);
  });

  it("BACKEND_ONLY 含旧 7 项 + watercolor + education_flat 共 9 项（2026-08-19 收敛扩容）", () => {
    expect([...BACKEND_ONLY_STYLE_KEYS]).toEqual([
      "corporate",
      "education_3d",
      "minimalist",
      "photorealistic",
      "documentary",
      "cinematic",
      "warm_casual",
      "watercolor",
      "education_flat",
    ]);
  });

  it("BACKEND_ONLY 每项都有中文 label（承载项显示兜底，不裸显英文 key）", () => {
    for (const key of BACKEND_ONLY_STYLE_KEYS) {
      const preset = BACKEND_ONLY_STYLE_PRESETS.find((p) => p.value === key);
      expect(preset, `${key} 应有承载预设`).toBeTruthy();
      expect(preset!.label, `${key} 应有中文 label`).toBeTruthy();
    }
  });

  it("resolveStyleValue：新 key 原样、旧值翻译、未知值不猜", () => {
    expect(resolveStyleValue("corporate")).toBe("corporate");
    expect(resolveStyleValue("3D render")).toBe("education_3d");
    expect(resolveStyleValue("flat illustration")).toBe("education_flat");
    expect(resolveStyleValue("professional")).toBe("corporate");
    expect(resolveStyleValue("casual")).toBe("warm_casual");
    expect(resolveStyleValue("watercolor")).toBe("watercolor");
    // 未知值原样返回，不猜也不静默改写
    expect(resolveStyleValue("某种没见过的风格")).toBe("某种没见过的风格");
    expect(resolveStyleValue("")).toBe("");
    expect(resolveStyleValue(null)).toBe("");
    expect(resolveStyleValue(undefined)).toBe("");
  });
});

describe("2026-08-19 项目级下拉收敛（两份反馈 T1 偏离项 1）", () => {
  it("PROJECT_STYLE_OPTIONS 基座 = 不设置 + 推荐 7 共 8 项（旧 7 项移出项目级 UI）", () => {
    expect(PROJECT_STYLE_OPTIONS.map((o) => [o.value, o.label])).toEqual([
      ["", "不设置"],
      ["precision_eng", "精密工程"],
      ["paper_collage", "纸艺拼贴"],
      ["papercraft_theater", "纸艺剧场"],
      ["character_3d", "3D 角色"],
      ["corporate_live", "商务实景"],
      ["isometric_info", "等距信息图"],
      ["whiteboard", "白板手绘"],
    ]);
    // 旧 7 项不再是 first-class 选项（不给新项目新选）
    for (const old of [
      "corporate",
      "education_3d",
      "minimalist",
      "photorealistic",
      "documentary",
      "cinematic",
      "warm_casual",
    ]) {
      expect(
        PROJECT_STYLE_OPTIONS.some((o) => o.value === old),
        `旧项 ${old} 不应再是项目级 first-class 选项`,
      ).toBe(false);
    }
  });

  it("projectStyleOptions：未设置 / 推荐值 → 基座 8 项，无承载项", () => {
    expect(projectStyleOptions("")).toHaveLength(8);
    expect(projectStyleOptions(null)).toHaveLength(8);
    expect(projectStyleOptions(undefined)).toHaveLength(8);
    expect(projectStyleOptions("whiteboard")).toHaveLength(8);
  });

  it("projectStyleOptions：存量旧 7 项值 → 基座 + 末尾追加承载项（不回落清空）", () => {
    const opts = projectStyleOptions("cinematic");
    expect(opts).toHaveLength(9);
    expect(opts.at(-1)).toEqual({ value: "cinematic", label: "电影感" });
    // 基座完整保留（承载项只追加不替换）
    expect(opts.slice(0, 8)).toEqual(PROJECT_STYLE_OPTIONS);
  });

  it("projectStyleOptions：watercolor / education_flat 承载显示（同旧 7 项模式）", () => {
    expect(projectStyleOptions("watercolor").at(-1)).toEqual({
      value: "watercolor",
      label: "水彩手绘",
    });
    expect(projectStyleOptions("education_flat").at(-1)).toEqual({
      value: "education_flat",
      label: "扁平插画",
    });
  });

  it("projectStyleOptions：未知值不承载也不猜（不凭空造选项）", () => {
    expect(projectStyleOptions("某种没见过的风格")).toHaveLength(8);
  });

  it("optionValueForStored：旧 7 项 / 后端保留项仍解析为非空 option value（保存不静默清空）", () => {
    for (const stored of [
      "cinematic",
      "corporate",
      "minimalist",
      "watercolor",
      "education_flat",
    ]) {
      const shown = optionValueForStored(stored);
      expect(shown, `存量值 ${stored} 不得解析为「不设置」`).not.toBe("");
      expect(
        projectStyleOptions(shown).some((o) => o.value === shown),
        `存量值 ${stored} 解析为 ${shown}，但 projectStyleOptions 里没有该 option`,
      ).toBe(true);
    }
    // 未知值仍是「不设置」（不猜）
    expect(optionValueForStored("某种没见过的风格")).toBe("");
  });
});
