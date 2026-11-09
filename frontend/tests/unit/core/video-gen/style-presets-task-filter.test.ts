/**
 * T1（2026-08-18 两份反馈 / P-01，Phase2 偏离修复）：任务级风格下拉五风格化。
 *
 * Phase 2（2026-08-17）执行偏离：任务级下拉做成 15 项兼容保留（default + 推荐 7
 * + 旧 7），未按引擎分流——后端 `style_presets.py` 早已导出 VIDEO_STYLE_KEYS /
 * IMAGE_STYLE_KEYS 两表（视频/图片各自五风格、三风格两用、四档专属），前端却
 * 无消费。本文件锁「任务级按引擎分表」的新语义：
 *
 * 1. 两表与后端真源逐项一致（跨端静态对齐的唯一防线——后端无 endpoint 暴露）；
 * 2. styleKeysForTaskType 分派：ai_image→图片表 / ai_video→视频表 /
 *    ai_visual/vimax→两表并集 7 项；remotion 由 style-visibility 层排除，
 *    不进本函数（类型级排除，typecheck 兜底）；
 * 3. 存量旧风格值不回落清空（承载项），切类型时受控值不在新选项集回落 default。
 */

import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  IMAGE_STYLE_KEYS,
  STYLE_PRESETS,
  styleAfterTypeChange,
  styleKeysForTaskType,
  stylePreviewImage,
  taskStyleOptions,
  VIDEO_STYLE_KEYS,
} from "@/core/video-gen/style-presets";

describe("T1: 五风格引擎分表（与后端 style_presets.py 同名常量逐项一致）", () => {
  it("VIDEO_STYLE_KEYS = 视频五风格（后端真源 tuple 快照）", () => {
    // 硬编码锁：backend/app/gateway/video_gen/style_presets.py 的
    // VIDEO_STYLE_KEYS = ("precision_eng", "paper_collage", "papercraft_theater",
    //                     "character_3d", "corporate_live")
    expect([...VIDEO_STYLE_KEYS]).toEqual([
      "precision_eng",
      "paper_collage",
      "papercraft_theater",
      "character_3d",
      "corporate_live",
    ]);
  });

  it("IMAGE_STYLE_KEYS = 图片五风格（后端真源 tuple 快照）", () => {
    // backend IMAGE_STYLE_KEYS = ("precision_eng", "paper_collage",
    //   "isometric_info", "whiteboard", "corporate_live")
    expect([...IMAGE_STYLE_KEYS]).toEqual([
      "precision_eng",
      "paper_collage",
      "isometric_info",
      "whiteboard",
      "corporate_live",
    ]);
  });

  it("两表互有专属档（不是同一张表）：纸艺剧场/3D角色仅视频，等距信息图/白板仅图片", () => {
    expect(VIDEO_STYLE_KEYS).not.toContain("isometric_info");
    expect(VIDEO_STYLE_KEYS).not.toContain("whiteboard");
    expect(IMAGE_STYLE_KEYS).not.toContain("papercraft_theater");
    expect(IMAGE_STYLE_KEYS).not.toContain("character_3d");
  });

  it("两表内每个 key 都能映射到 STYLE_PRESETS 预设（有 label 可渲染）", () => {
    const known = new Set(STYLE_PRESETS.map((p) => p.value));
    for (const key of [...VIDEO_STYLE_KEYS, ...IMAGE_STYLE_KEYS]) {
      expect(known.has(key), `key ${key} 在 STYLE_PRESETS 里查无预设`).toBe(
        true,
      );
    }
  });
});

describe("T1: styleKeysForTaskType 按任务引擎分派", () => {
  it("ai_image → 图片表 5 项", () => {
    expect([...styleKeysForTaskType("ai_image")]).toEqual([
      ...IMAGE_STYLE_KEYS,
    ]);
  });

  it("ai_video → 视频表 5 项", () => {
    expect([...styleKeysForTaskType("ai_video")]).toEqual([
      ...VIDEO_STYLE_KEYS,
    ]);
  });

  it("ai_visual（历史别名）→ 两表并集 7 项（图片+视频链路都可能走）", () => {
    const keys = styleKeysForTaskType("ai_visual");
    expect(keys).toHaveLength(7);
    expect(new Set(keys)).toEqual(
      new Set([...VIDEO_STYLE_KEYS, ...IMAGE_STYLE_KEYS]),
    );
  });

  it("vimax → 两表并集 7 项，与「推荐」组集合一致", () => {
    const keys = styleKeysForTaskType("vimax");
    expect(keys).toHaveLength(7);
    const recommended = STYLE_PRESETS.filter((p) => p.group === "推荐").map(
      (p) => p.value,
    );
    expect(new Set(keys)).toEqual(new Set(recommended));
  });
});

describe("T1: taskStyleOptions 任务级下拉选项（默认 + 引擎表 + 旧值承载）", () => {
  it("ai_image → 默认 + 5 图片风格 = 6 项；不含视频专属档与旧 7 项", () => {
    const opts = taskStyleOptions("ai_image", "default");
    expect(opts.map((o) => o.value)).toEqual([
      "default",
      "precision_eng",
      "paper_collage",
      "isometric_info",
      "whiteboard",
      "corporate_live",
    ]);
    // 旧 7 项（08-01 清单）已移出任务级 UI
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
        opts.some((o) => o.value === old),
        `旧项 ${old} 不应出现`,
      ).toBe(false);
    }
  });

  it("ai_video → 默认 + 5 视频风格（两表确实不同）", () => {
    const opts = taskStyleOptions("ai_video", "default");
    expect(opts.map((o) => o.value)).toEqual([
      "default",
      "precision_eng",
      "paper_collage",
      "papercraft_theater",
      "character_3d",
      "corporate_live",
    ]);
  });

  it("vimax → 默认 + 并集 7 项 = 8 项", () => {
    expect(taskStyleOptions("vimax", "default")).toHaveLength(8);
  });

  it("存量承载：受控值是旧风格（不在引擎表）→ 追加承载项，不回落清空", () => {
    const opts = taskStyleOptions("ai_image", "cinematic");
    const last = opts.at(-1);
    expect(last?.value, "末项应为承载的旧值 cinematic").toBe("cinematic");
    expect(last?.label).toBe("电影感");
    // 前六项仍是完整的默认 + 图片表（承载项只追加不替换）
    expect(opts.slice(0, 6).map((o) => o.value)).toEqual([
      "default",
      ...IMAGE_STYLE_KEYS,
    ]);
  });

  it("存量承载：旧别名（professional）按原始值追加，label 取解析目标（受控显示须与 state 逐字相等）", () => {
    const opts = taskStyleOptions("ai_video", "professional");
    expect(opts.at(-1)?.value).toBe("professional");
    expect(opts.at(-1)?.label).toBe("商务专业");
  });

  it("存量承载：watercolor（后端保留、任务级 UI 无此项）也能承载显示", () => {
    const opts = taskStyleOptions("ai_image", "watercolor");
    expect(opts.at(-1)?.value).toBe("watercolor");
    expect(opts.at(-1)?.label).toBe("水彩手绘");
  });

  it("受控值在引擎表内 → 不追加承载项（选项数不变）", () => {
    expect(taskStyleOptions("ai_image", "whiteboard")).toHaveLength(6);
    expect(taskStyleOptions("ai_video", "papercraft_theater")).toHaveLength(6);
  });
});

describe("T1: styleAfterTypeChange 切类型后的风格回落", () => {
  it("当前值不在新类型选项集 → 回落 default（如 ai_video 纸艺剧场 → ai_image）", () => {
    expect(styleAfterTypeChange("ai_image", "papercraft_theater")).toBe(
      "default",
    );
    expect(styleAfterTypeChange("ai_video", "whiteboard")).toBe("default");
  });

  it("当前值在新类型选项集（三风格两用）→ 保留", () => {
    expect(styleAfterTypeChange("ai_image", "precision_eng")).toBe(
      "precision_eng",
    );
    expect(styleAfterTypeChange("ai_video", "corporate_live")).toBe(
      "corporate_live",
    );
    expect(styleAfterTypeChange("vimax", "papercraft_theater")).toBe(
      "papercraft_theater",
    );
  });

  it("default 与旧别名也按同一规则判（professional→corporate 不在视频表→default）", () => {
    expect(styleAfterTypeChange("ai_image", "default")).toBe("default");
    expect(styleAfterTypeChange("ai_video", "professional")).toBe("default");
    // professional→corporate 在职场组但不在五风格表——切 vimax 并集同样不含 → default
    expect(styleAfterTypeChange("vimax", "cinematic")).toBe("default");
  });
});

// ── 风格预览图（2026-09-09 设计：任务创建对话框风格下拉的可视化参考）──────
//
// 三条防线（设计 §五）：资产存在性挡「没跑生成脚本就发版」；description 非空
// （用户向文案，不做 STYLE_PREFIXES 逐字对齐——见 style-presets.ts 取舍说明）；
// stylePreviewImage 判定与 default / 承载项排除。

describe("风格预览图：静态资产 + stylePreviewImage 映射", () => {
  const UNION_KEYS = [...new Set([...VIDEO_STYLE_KEYS, ...IMAGE_STYLE_KEYS])];

  it("两表并集 = 7 个唯一 key（三风格两用，四档专属）", () => {
    expect(UNION_KEYS).toHaveLength(7);
  });

  it("资产存在性：7 个 key × public/style-previews/<key>.jpg 必须存在", () => {
    for (const key of UNION_KEYS) {
      const p = resolve(
        __dirname,
        "../../../..",
        "public",
        "style-previews",
        `${key}.jpg`,
      );
      expect(
        existsSync(p),
        `缺 ${p}——先跑 backend/scripts/generate_style_previews.py 生成并提交 git`,
      ).toBe(true);
    }
  });

  it("描述文案：7 个 first-class key 的用户向 description 非空", () => {
    for (const key of UNION_KEYS) {
      const preset = STYLE_PRESETS.find((p) => p.value === key);
      expect(preset, `key ${key} 应在 STYLE_PRESETS 有预设`).toBeDefined();
      expect(
        preset?.description?.trim(),
        `key ${key} 缺用户向 description`,
      ).toBeTruthy();
    }
  });

  it("stylePreviewImage：7 key 返回 /style-previews/<key>.jpg?v=<N>，版本号全表一致", () => {
    const urls = UNION_KEYS.map((k) => stylePreviewImage(k));
    UNION_KEYS.forEach((key, i) => {
      // 版本号用 \d+ 而非硬编码 1：重出图 bump 版本不破测试；一致性由下一断言守
      expect(urls[i]).toMatch(
        new RegExp(`^/style-previews/${key}\\.jpg\\?v=\\d+$`),
      );
    });
    expect(new Set(urls.map((u) => u!.split("?v=")[1])).size).toBe(1);
  });

  it("stylePreviewImage：default / 跟随项目 / 存量承载项 / 未知值 → null", () => {
    for (const key of [
      "default",
      "",
      "follow_project",
      // 存量承载项（旧 7 项 / watercolor / 旧别名）：只读兼容不投入预览资产
      "cinematic",
      "watercolor",
      "professional",
      "not_a_style_key",
    ]) {
      expect(stylePreviewImage(key), `key ${key} 不应有预览图`).toBeNull();
    }
  });
});
