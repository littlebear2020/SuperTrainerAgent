/**
 * T9（2026-09-10 独立管线计划）+ 2026-09-11 审查 M9 修订：
 * layout-content-schema 一致性守卫 + 占位检查分类 + 素材来源前缀判定。
 *
 * 字段一致性用**跨仓守卫**——直接解析后端 course_script.py 的
 * LAYOUT_CONTENT_FIELDS 源码逐版式比对（修前 toMatchInlineSnapshot(自身) 是
 * 自我快照，本次漂移全程绿灯，防不住任何一方改动）。
 *
 * 运行前提：**monorepo 检出**——本测试直接读 `backend/` 源码，frontend 单独克隆无法运行。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  LAYOUT_CONTENT_SCHEMA,
  checkPlaceholderFrames,
  hasAssetArea,
  isAiMaterialFrame,
  isEmbeddedOrUploadedPath,
  isImageSlotLayout,
} from "./layout-content-schema";

const HERE = dirname(fileURLToPath(import.meta.url));
const BACKEND_FIELDS_SRC = resolve(
  HERE,
  "../../../../backend/app/gateway/video_gen/orchestration/course_script.py",
);

/** 解析后端 LAYOUT_CONTENT_FIELDS（单行条目：`    "Layout": ("f1", "f2"),`）。
 * 只取该 dict 字面量块内——全文件扫会把 _MODE_RULES 等同形条目误收进来。 */
function backendLayoutFields(): Record<string, string[]> {
  const src = readFileSync(BACKEND_FIELDS_SRC, "utf-8");
  const start = src.indexOf("LAYOUT_CONTENT_FIELDS");
  const blockEnd = src.indexOf("\n}\n", start);
  if (start < 0 || blockEnd < 0)
    throw new Error(
      "backend LAYOUT_CONTENT_FIELDS 未找到（course_script.py 结构变化？）",
    );
  const block = src.slice(start, blockEnd);
  const out: Record<string, string[]> = {};
  for (const m of block.matchAll(/^    "(\w+)": \(([^)]*)\),$/gm)) {
    out[m[1]!] = [...m[2]!.matchAll(/"(\w+)"/g)].map((x) => x[1]!);
  }
  return out;
}

describe("layout-content-schema（T9 + M9 跨仓守卫）", () => {
  it("19 版式全覆盖（含 VideoClipPage 编辑 schema）", () => {
    expect(Object.keys(LAYOUT_CONTENT_SCHEMA)).toHaveLength(19);
  });

  it("与后端 LAYOUT_CONTENT_FIELDS 逐版式逐字段一致", () => {
    const backend = backendLayoutFields();
    expect(Object.keys(backend)).not.toHaveLength(0);
    expect(Object.keys(LAYOUT_CONTENT_SCHEMA).sort()).toEqual(
      Object.keys(backend).sort(),
    );
    for (const [layout, fields] of Object.entries(LAYOUT_CONTENT_SCHEMA)) {
      expect(
        fields.map((f) => f.key),
        `前端 schema「${layout}」与后端白名单不一致（漂移需双侧同步改 + 模板 SceneContent 核对）`,
      ).toEqual(backend[layout]);
    }
  });

  it("2026-09-11 漂移修复：instructor/note/bullets 已补齐", () => {
    expect(LAYOUT_CONTENT_SCHEMA.CoverPage?.map((f) => f.key)).toContain(
      "instructor",
    );
    expect(LAYOUT_CONTENT_SCHEMA.CodePage?.map((f) => f.key)).toContain(
      "bullets",
    );
    expect(LAYOUT_CONTENT_SCHEMA.ChartPage?.map((f) => f.key)).toContain(
      "note",
    );
    expect(LAYOUT_CONTENT_SCHEMA.FullVisualPage?.map((f) => f.key)).toContain(
      "note",
    );
  });

  it("图槽版式判定", () => {
    expect(isImageSlotLayout("ImageTextLeft")).toBe(true);
    expect(isImageSlotLayout("QuotePage")).toBe(true);
    expect(isImageSlotLayout("EndingPage")).toBe(true);
    expect(isImageSlotLayout("FullVisualPage")).toBe(true);
    expect(isImageSlotLayout("ConceptPage")).toBe(false);
    expect(isImageSlotLayout(null)).toBe(false);
    expect(hasAssetArea("VideoClipPage")).toBe(true);
    expect(hasAssetArea("ConceptPage")).toBe(false);
  });
});

describe("素材来源前缀判定（B2，2026-09-11 审查）", () => {
  it("嵌入/上传前缀为真；AI 本地产物（img_/shot_）为假", () => {
    expect(isEmbeddedOrUploadedPath("visuals/embedded/img_1.png")).toBe(true);
    expect(isEmbeddedOrUploadedPath("visuals/frame_0007.png")).toBe(true);
    expect(isEmbeddedOrUploadedPath("visuals/frame_0012.mp4")).toBe(true);
    // AI 生图/生视频本地化产物——修前被 !!path 误判成嵌入/上传，重生入口消失
    expect(isEmbeddedOrUploadedPath("visuals/img_0003.png")).toBe(false);
    expect(isEmbeddedOrUploadedPath("visuals/shot_0005.mp4")).toBe(false);
    expect(isEmbeddedOrUploadedPath(null)).toBe(false);
    expect(isEmbeddedOrUploadedPath(undefined)).toBe(false);
    expect(isEmbeddedOrUploadedPath("")).toBe(false);
  });
});

describe("渲染确认占位检查（T14 纯逻辑，决策 16）", () => {
  it("分类正确：占位 / 待生成 / 有图", () => {
    const result = checkPlaceholderFrames([
      // 占位：图槽无素材无生成计划
      { index: 0, layout: "ImageTextLeft" },
      // 待生成 ≠ 占位：图槽 + prompt + ai_image
      {
        index: 1,
        layout: "ImageTextRight",
        image_prompt: "示意图",
        visual_source: "ai_image",
      },
      // 有图（嵌入/上传 local）
      {
        index: 2,
        layout: "QuotePage",
        local_visual_path: "visuals/embedded/img_1.png",
      },
      // 有图（远端生成图）
      { index: 3, layout: "EndingPage", visual_url: "https://cdn/x.png" },
      // 非槽版式不进占位统计
      { index: 4, layout: "ConceptPage" },
      // 视频占位：VideoClipPage 无素材
      { index: 5, layout: "VideoClipPage" },
      // 视频 待生成
      {
        index: 6,
        layout: "VideoClipPage",
        image_prompt: "镜头词",
        visual_source: "ai_video",
      },
      // 视频 已上传
      {
        index: 7,
        layout: "VideoClipPage",
        local_visual_path: "visuals/frame_007.mp4",
      },
      // 图槽 + prompt 但未设 visual_source（渲染不会生成）→ 仍算占位
      { index: 8, layout: "ImageTextLeft", image_prompt: "词" },
    ]);
    expect(result.imagePlaceholders).toEqual([0, 8]);
    expect(result.videoPlaceholders).toEqual([5]);
  });
});

describe("占位判定槽位对齐（m8，2026-09-11 二轮审查）", () => {
  it("切版式后残留源不误判「待生成」——后端四条件对齐", () => {
    const result = checkPlaceholderFrames([
      // ImageTextLeft + 残留 ai_video 源（后端不会生成）→ 占位（修前漏报）
      {
        index: 0,
        layout: "ImageTextLeft",
        image_prompt: "词",
        visual_source: "ai_video",
      },
      // VideoClipPage + 残留 ai_image 源 → 视频占位（修前漏报）
      {
        index: 1,
        layout: "VideoClipPage",
        image_prompt: "词",
        visual_source: "ai_image",
      },
      // 槽位 + 匹配源 + prompt → 待生成（非占位）不变
      {
        index: 2,
        layout: "ImageTextRight",
        image_prompt: "词",
        visual_source: "ai_image",
      },
      // 非槽版式 + ai_image 残留源 → 不进任何占位统计（版式无图槽）
      {
        index: 3,
        layout: "ConceptPage",
        image_prompt: "词",
        visual_source: "ai_image",
      },
    ]);
    expect(result.imagePlaceholders).toEqual([0]);
    expect(result.videoPlaceholders).toEqual([1]);
  });
});

describe("AI 素材帧判定（m10，2026-09-11 二轮审查）", () => {
  it("AI 本地产物或按槽位开启源；素材在但源已清也算（详情页/分镜页共用）", () => {
    expect(
      isAiMaterialFrame({ local_visual_path: "visuals/img_0003.png" }, false),
    ).toBe(true);
    expect(isAiMaterialFrame({ visual_source: "ai_image" }, false)).toBe(true);
    expect(isAiMaterialFrame({ visual_source: "ai_video" }, true)).toBe(true);
    // AI 素材在但源已清（B3 取消后）——修前详情页判 false、分镜页判 true
    expect(isAiMaterialFrame({ visual_url: "https://cdn/x.png" }, false)).toBe(
      true,
    );
    // 嵌入/上传素材不算
    expect(
      isAiMaterialFrame(
        { local_visual_path: "visuals/embedded/img_1.png" },
        false,
      ),
    ).toBe(false);
    expect(
      isAiMaterialFrame({ local_visual_path: "visuals/frame_0007.png" }, false),
    ).toBe(false);
    // 槽位不匹配的源不算（视频槽只认 ai_video）
    expect(isAiMaterialFrame({ visual_source: "ai_image" }, true)).toBe(false);
    expect(isAiMaterialFrame({}, false)).toBe(false);
  });
});
