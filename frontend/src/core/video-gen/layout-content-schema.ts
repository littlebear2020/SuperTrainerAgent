/**
 * remotion_course 版式结构化内容编辑 schema（2026-09-10 独立管线计划 T9）。
 *
 * 单一事实源：字段名与后端 LAYOUT_CONTENT_FIELDS
 * （backend/app/gateway/video_gen/orchestration/course_script.py）逐字一致——
 * 后者又与模板契约 video-templates/course-standard/src/content.tsx 的 SceneContent
 * 对齐。vitest 快照防三方漂移（本文件 / 后端白名单 / 模板接口）。
 *
 * layout_content 永不承载图片数据——图槽素材走 local_visual_path / visual_url
 * （素材区通道），与 content 是两条独立通道。
 */

export type LayoutContentFieldKind =
  | "text"
  | "textarea"
  | "list"
  | "steps"
  | "chart"
  | "chapters"
  | "cards"
  | "options"
  | "code"
  | "number"
  | "instructor";

export interface LayoutContentFieldDef {
  /** 字段名（与后端白名单/模板 SceneContent 逐字一致）。 */
  key: string;
  /** 中文展示名。 */
  label: string;
  kind: LayoutContentFieldKind;
}

/** 图槽版式集合（§3.4）：素材区仅这些版式 + VideoClipPage 显示。 */
export const IMAGE_SLOT_LAYOUTS: ReadonlySet<string> = new Set([
  "ImageTextLeft",
  "ImageTextRight",
  "QuotePage",
  "EndingPage",
  "FullVisualPage",
]);

/** VideoClipPage（16:9 视频槽）——素材区视频入口（决策 9）。 */
export const VIDEO_CLIP_LAYOUT = "VideoClipPage";

export const LAYOUT_CONTENT_SCHEMA: Record<string, LayoutContentFieldDef[]> = {
  CoverPage: [
    { key: "title", label: "主标题", kind: "text" },
    { key: "subtitle", label: "副标题", kind: "text" },
    { key: "brandName", label: "品牌名", kind: "text" },
    { key: "courseNo", label: "课程编号", kind: "text" },
    { key: "series", label: "系列名", kind: "text" },
    // 2026-09-11 审查 M9：后端白名单/模板 SceneContent 均有——修前前端漏。
    { key: "instructor", label: "讲师", kind: "instructor" },
  ],
  ObjectivesPage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "bullets", label: "学习目标", kind: "list" },
  ],
  OutlinePage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "chapters", label: "章节列表", kind: "chapters" },
    { key: "currentChapter", label: "高亮章（0 起）", kind: "number" },
  ],
  ChapterDivider: [
    { key: "chapterNo", label: "章序号（1 起）", kind: "number" },
    { key: "chapterTotal", label: "总章数", kind: "number" },
    { key: "title", label: "章标题", kind: "text" },
    { key: "lead", label: "引导语", kind: "textarea" },
  ],
  ConceptPage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "lead", label: "引导语", kind: "textarea" },
    { key: "bullets", label: "要点", kind: "list" },
  ],
  ImageTextLeft: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "bullets", label: "要点", kind: "list" },
  ],
  ImageTextRight: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "bullets", label: "要点", kind: "list" },
  ],
  FullVisualPage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "subtitle", label: "副标题", kind: "text" },
    { key: "note", label: "底注", kind: "text" },
  ],
  VideoClipPage: [
    { key: "title", label: "标题", kind: "text" },
    { key: "badge", label: "徽标（如 视频 · 02:36）", kind: "text" },
  ],
  ComparisonPage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "leftLabel", label: "左侧标签", kind: "text" },
    { key: "rightLabel", label: "右侧标签", kind: "text" },
    { key: "left", label: "左侧要点", kind: "list" },
    { key: "right", label: "右侧要点", kind: "list" },
  ],
  ChartPage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "chart", label: "图表数据", kind: "chart" },
    { key: "headline", label: "结论大数字", kind: "text" },
    { key: "conclusion", label: "结论", kind: "textarea" },
    { key: "note", label: "数据注脚", kind: "text" },
  ],
  ProcessPage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "steps", label: "步骤", kind: "steps" },
    { key: "currentStep", label: "当前步（0 起）", kind: "number" },
  ],
  CodePage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "fileName", label: "文件名", kind: "text" },
    { key: "code", label: "代码", kind: "code" },
    // 2026-09-11 审查 M9：后端白名单/模板（Code 侧栏 bullets）均有——修前前端漏。
    { key: "bullets", label: "侧栏要点", kind: "list" },
  ],
  QuotePage: [
    { key: "quote", label: "金句", kind: "textarea" },
    { key: "source", label: "出处", kind: "text" },
  ],
  CasePage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "cards", label: "案例卡片", kind: "cards" },
  ],
  SummaryPage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "bullets", label: "要点回顾", kind: "list" },
  ],
  QuizPage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "question", label: "问题", kind: "textarea" },
    { key: "options", label: "选项", kind: "options" },
  ],
  EndingPage: [
    { key: "kicker", label: "小标签", kind: "text" },
    { key: "recap", label: "本讲回顾", kind: "textarea" },
    { key: "nextLabel", label: "下集标签", kind: "text" },
    { key: "nextTitle", label: "下集标题", kind: "text" },
    { key: "nextSubtitle", label: "下集副标题", kind: "text" },
    { key: "cta", label: "行动号召", kind: "text" },
  ],
  EndCard: [
    { key: "brandName", label: "品牌名", kind: "text" },
    { key: "title", label: "标题", kind: "text" },
    { key: "follow", label: "关注引导", kind: "text" },
    { key: "cta", label: "行动号召", kind: "text" },
    { key: "copyright", label: "版权行", kind: "text" },
  ],
};

/** 版式是否有图槽（素材区显示判定）。 */
export function isImageSlotLayout(layout: string | null | undefined): boolean {
  return !!layout && IMAGE_SLOT_LAYOUTS.has(layout);
}

/** 版式是否显示素材区（图槽或视频槽）。 */
export function hasAssetArea(layout: string | null | undefined): boolean {
  return isImageSlotLayout(layout) || layout === VIDEO_CLIP_LAYOUT;
}

/**
 * 渲染确认占位检查（决策 16，§7 分类规则）：
 * 「将渲染占位」= 图槽/VideoClipPage 版式 且无 local_visual_path 且无 visual_url
 * 且**非**「有 prompt 待生成」状态（visual_source 已设 ai_image/ai_video 且
 * image_prompt 非空 → 渲染时会生成，不算占位）。
 */
export interface PlaceholderCheckFrame {
  index: number;
  layout?: string | null;
  local_visual_path?: string | null;
  visual_url?: string | null;
  visual_source?: string | null;
  image_prompt?: string | null;
}

export interface PlaceholderCheckResult {
  /** 将渲染图片占位的帧（缺图且不会生成）。 */
  imagePlaceholders: number[];
  /** 将渲染视频占位的帧（VideoClipPage 无视频且不会生成）。 */
  videoPlaceholders: number[];
}

export function checkPlaceholderFrames(
  frames: PlaceholderCheckFrame[],
): PlaceholderCheckResult {
  const imagePlaceholders: number[] = [];
  const videoPlaceholders: number[] = [];
  for (const f of frames) {
    const hasMaterial = !!f.local_visual_path || !!f.visual_url;
    // m8（2026-09-11 二轮审查）：「待生成」须与后端 image_slot_target/video_clip_target
    // 四条件逐字对齐（版式 + 源 + prompt）——修前不查版式，切版式后残留源被误判
    // 「会生成」而漏报占位（后端不会生成非槽位帧）。
    const pendingGenerate =
      !!f.image_prompt?.trim() &&
      ((isImageSlotLayout(f.layout) && f.visual_source === "ai_image") ||
        (f.layout === VIDEO_CLIP_LAYOUT && f.visual_source === "ai_video"));
    if (hasMaterial || pendingGenerate) continue;
    if (f.layout === VIDEO_CLIP_LAYOUT) {
      videoPlaceholders.push(f.index);
    } else if (isImageSlotLayout(f.layout)) {
      imagePlaceholders.push(f.index);
    }
  }
  return { imagePlaceholders, videoPlaceholders };
}

/**
 * 素材本地路径是否为「嵌入/上传」来源（B2，2026-09-11 审查）。
 *
 * 三类本地路径按前缀区分（与后端约定逐字一致）：
 * - `visuals/embedded/`：docx 内嵌图（认领搬入）
 * - `visuals/frame_`：用户上传（upload-visual 端点，后端 `_USER_VISUAL_PREFIX`）
 * - `visuals/img_` / `visuals/shot_`：AI 生图/生视频本地化产物——**不是**用户素材
 *
 * 修前用 `!!frame.local_visual_path` 判定，首轮渲染后所有 AI 帧被误判成
 * 「嵌入/上传」→「重新生成配图/视频」入口消失（后端 P6 守卫也只拒前两种前缀）。
 */
export function isEmbeddedOrUploadedPath(
  path: string | null | undefined,
): boolean {
  if (!path) return false;
  return (
    path.startsWith("visuals/embedded/") || path.startsWith("visuals/frame_")
  );
}

/**
 * AI 素材帧判定（m10，2026-09-11 二轮审查：详情页/分镜页共用同一口径——修前
 * 两处 isAiMaterial 写法不一，AI 素材但 visual_source 已清的帧入口表现相反）。
 * AI 本地产物（img_/shot_）或已按槽位开启 AI 源（素材可能尚未生成）。
 */
export function isAiMaterialFrame(
  frame: {
    local_visual_path?: string | null;
    visual_url?: string | null;
    visual_source?: string | null;
  },
  isVideoSlot: boolean,
): boolean {
  return (
    !isEmbeddedOrUploadedPath(frame.local_visual_path) &&
    (!!frame.local_visual_path ||
      !!frame.visual_url ||
      frame.visual_source === (isVideoSlot ? "ai_video" : "ai_image"))
  );
}
