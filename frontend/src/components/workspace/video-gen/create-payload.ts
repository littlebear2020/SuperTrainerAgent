import type { CreateVideoRequest } from "@/core/video-gen/types";

export const TARGET_DURATION_MIN = 5;
export const TARGET_DURATION_MAX = 600;
export const SECONDS_PER_FRAME_MIN = 3;
export const SECONDS_PER_FRAME_MAX = 8;

export interface CreatePayloadInput {
  taskType: CreateVideoRequest["video_type"];
  mode: CreateVideoRequest["mode"];
  /** 创建时自定义任务标题（可选；空/纯空白不下发，保持既有请求体形状）。 */
  title?: string;
  script: string;
  topic: string;
  lang: string;
  aspect: CreateVideoRequest["aspect"];
  style: string;
  subtitleStyle: CreateVideoRequest["subtitle_style"];
  bgm: string | null;
  voiceId: string | null;
  outputFormat: CreateVideoRequest["output_format"];
  quality: CreateVideoRequest["quality"];
  /** T-E11（D6 review #1）：null = 跟随（项目 tts_speed > config 缺省）——
   * 载荷层不下发 tts_speed（同 voiceId 的 follow_project → undefined 处理），
   * 否则挂项目任务的 payload 恒含显式语速、项目级语速被架空。 */
  ttsSpeed: number | null;
  /** 0..1 — callers pass `bgmVolume / 100` (slider is 0-100). */
  bgmVolume: number;
  bgmDucking: boolean;
  /** Task 6：任务级生成模型；null/undefined = 不指定（跟随后端 config 默认且保留降级链）。 */
  imageProvider?: string | null;
  videoProvider?: string | null;
  courseProjectId?: string | null;
  /** 默认 false：走分镜草稿→确认→渲染两段式（修「生成视频跳分镜确认」）。
   * true 才直渲（保留给老行为/API）。 */
  autoRender?: boolean;
  /** 目标总时长（秒）：引导 generate 模式生成镜数（下限保证，非双向约束——仅保证镜数足够撑满目标，不自动减镜/扩写）+ 事后偏差提示。不传则不约束。 */
  targetDuration?: number;
  /** 每图时长（秒，3-8）：用户自选帧密度，胜过 8s 下限。generate 模式影响镜数；fixed 模式合并长脚本到 ceil(target/spf) 段。不传则按 8s 下限。 */
  secondsPerFrame?: number;
  /** C1: 上传文档默认 LLM 改写成干净旁白；可关（短输入/逐字保留）。默认 true。 */
  aiNormalizeScript?: boolean;
  /** T3（2026-09-01）：任务级图文镜占比上限（%）。undefined = 不下发（后端默认 30%）。 */
  graphicRatio?: 30 | 40 | 50 | 70;
  /** T7（2026-09-10 独立管线计划）：remotion_course 解析双档；undefined = 不下发（后端默认 faithful）。 */
  courseParseMode?: "faithful" | "rewrite";
  /** T6：docx 图片暂存 extract_id；null/undefined = 不下发。 */
  scriptExtractId?: string | null;
}

/** 构建 POST /api/video/create 的 payload。
 *
 * **auto_render 默认 false**：修复「生成视频跳过分镜确认」——先出分镜草稿
 * （DRAFT/STORY_READY），用户在分镜页编辑后点「开始渲染」(POST /render) 才直渲。
 * 后端 `service.py` Entry 2 路径已支持此两段式流程。 */
export function buildCreatePayload(
  input: CreatePayloadInput,
): CreateVideoRequest {
  const trimmedTitle = input.title?.trim() ?? "";
  const title = trimmedTitle ? trimmedTitle.slice(0, 80) : "";
  return {
    video_type: input.taskType,
    mode: input.mode,
    ...(title ? { title } : {}),
    script: input.script,
    topic: input.topic,
    lang: input.lang,
    // Minor（2026-09-11 审查）：remotion_course 创建页隐藏了比例选择（渲染恒
    // 1920×1080 横屏），但 state 默认 "9:16" 仍会下发——字幕重排的竖屏启发式读
    // 这份 config，横屏成片配竖屏 config 会字幕过窄。载荷层统一收口为 16:9。
    aspect: input.taskType === "remotion_course" ? "16:9" : input.aspect,
    // C7（决策 3/5）：style 哨兵 follow_project 按原值下发（后端 _resolve_effective_style
    // 按未显式处理——卡内定义的防御路径）；音色/字幕哨兵在后端 schema 无对应处理
    // （voice_id 自由串会被当显式值、subtitle_style Literal 会 422），故在载荷层归一为
    // 等价的「未设值」（undefined / "default"）——对后端解析器与哨兵原值语义完全一致。
    style: input.style,
    subtitle_style:
      input.subtitleStyle === "follow_project"
        ? "default"
        : input.subtitleStyle,
    bgm: input.bgm,
    voice_id:
      input.voiceId === "follow_project"
        ? undefined
        : (input.voiceId ?? undefined),
    output_format: input.outputFormat,
    quality: input.quality,
    // T-E11（D6 review #1）：null（跟随）**不下发该字段**（同 image/videoProvider
    // 惯例）——后端三级链（任务显式 > 项目 > config）按缺省解析；修前恒显式
    // 1.0 下发把项目级语速在 UI 主路径架空。
    ...(input.ttsSpeed != null ? { tts_speed: input.ttsSpeed } : {}),
    bgm_volume: input.bgmVolume,
    bgm_ducking: input.bgmDucking,
    // 不指定时**不下发该字段**（而非下发 null）：保持与改动前完全一致的请求体，
    // 避免后端把「显式 null」与「未指定」当成两回事。
    ...(input.imageProvider ? { image_provider: input.imageProvider } : {}),
    ...(input.videoProvider ? { video_provider: input.videoProvider } : {}),
    auto_render: input.autoRender ?? false,
    target_duration: input.targetDuration,
    seconds_per_frame: input.secondsPerFrame,
    ai_normalize_script: input.aiNormalizeScript ?? true,
    course_project_id: input.courseProjectId ?? undefined,
    // T3：默认 30 与未指定都不下发——请求体形状与改动前完全一致（后端缺省承担
    // 30%，语义等价：上限≠保障，30 即默认值）。
    ...(input.graphicRatio && input.graphicRatio !== 30
      ? { graphic_ratio: input.graphicRatio }
      : {}),
    // T7/T6（独立管线）：仅 remotion_course 消费；faithful（默认）不下发保持请求体
    // 形状与改动前一致。
    ...(input.courseParseMode && input.courseParseMode !== "faithful"
      ? { course_parse_mode: input.courseParseMode }
      : {}),
    ...(input.scriptExtractId
      ? { script_extract_id: input.scriptExtractId }
      : {}),
  };
}

/**
 * 2026-10-20 Task 5：target_duration 只在语义可见的形态下发——
 * generate 模式，或 vimax fixed 模式；隐藏控件的残留值/NaN/越界一律不下发，
 * 避免切换 taskType/mode 后悄悄改变后端分镜行为。
 */
export function resolveTargetDurationForPayload(
  taskType: CreateVideoRequest["video_type"],
  mode: CreateVideoRequest["mode"],
  raw: number | "" | null | undefined,
): number | undefined {
  const visible = isTargetDurationVisible(taskType, mode);
  if (!visible || raw === "" || raw === null || raw === undefined)
    return undefined;
  const value = Number(raw);
  if (!isValidTargetDuration(value)) return undefined;
  return value;
}

/** 目标时长控件与载荷下发的共享可见性口径（防 UI/载荷双写漂移）。 */
export function isTargetDurationVisible(
  taskType: CreateVideoRequest["video_type"],
  mode: CreateVideoRequest["mode"],
): boolean {
  return mode === "generate" || (taskType === "vimax" && mode === "fixed");
}

/** seconds_per_frame 仅 generate 模式可见；隐藏残留/NaN/越界一律不下发。 */
export function resolveSecondsPerFrameForPayload(
  mode: CreateVideoRequest["mode"],
  raw: number | "" | null | undefined,
): number | undefined {
  if (mode !== "generate" || raw === "" || raw === null || raw === undefined) {
    return undefined;
  }
  const value = Number(raw);
  if (
    !Number.isFinite(value) ||
    value < SECONDS_PER_FRAME_MIN ||
    value > SECONDS_PER_FRAME_MAX
  )
    return undefined;
  return value;
}

/** 空值合法（可选字段）；非空必须是有限数且落在 5–600 秒。 */
export function isValidTargetDuration(
  raw: number | "" | null | undefined,
): boolean {
  if (raw === "" || raw === null || raw === undefined) return true;
  const value = Number(raw);
  return (
    Number.isFinite(value) &&
    value >= TARGET_DURATION_MIN &&
    value <= TARGET_DURATION_MAX
  );
}
