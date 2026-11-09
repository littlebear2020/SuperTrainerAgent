/**
 * Video generation module types.
 */

/** Task status from unified_tasks. */
export interface VideoTaskBrief {
  id: string;
  user_id: string;
  domain: string;
  task_type:
    | "ai_image"
    | "ai_video"
    | "ai_visual"
    | "remotion"
    | "remotion_course"
    | "vimax";
  status: string;
  progress: number;
  title: string | null;
  error_message: string | null;
  retry_count: number;
  dirty_frames: number[];
  created_at: string | null;
  updated_at: string | null;
  thumbnail_url: string | null;
  /** 2026-09-13 死链修复：产物缺失派生标注（后端响应层计算不落库，仅 video_gen
   *  ready 态按 output_path 存在性赋值；缺省 = 后端未标注/其他域，恒 false）。 */
  artifact_missing?: boolean;
  /** 批次 C (2026-08-13)：每任务花费（后端 task_cost 计算，null=无法计价）。 */
  cost: number | null;
  currency: string | null;
  course_project_id?: string | null;
  /** 2026-10-20 Task 4：任务详情摘要——盘面 storyboard.json 帧数（列表端点不读盘）。 */
  frame_count?: number | null;
  /** 盘面 storyboard total_duration（秒）；未知 = null。 */
  total_duration?: number | null;
  /** result.vimax_shots 渲染后镜头 manifest 数（非草稿帧数）。 */
  shot_count?: number | null;
  /** `result.vimax_shots` manifest 非空即可逐镜重做（与 ready/failed 状态解耦）。 */
  shots_ready?: boolean;
}

/** Unified video creation request (POST /api/video/create). */
export interface CreateVideoRequest {
  // R2-2（2026-10-07，D4 拍板）：新建入口删 "remotion"——chat-handoff 链路已无
  // 前端提交方；展示侧 VideoTaskBrief.task_type / VideoTaskType 联合保留该值
  //（存量任务列表/详情仍需渲染）。
  video_type: "ai_image" | "ai_video" | "remotion_course" | "vimax";
  mode: "generate" | "fixed";
  /** 2026-10-20 Task 5：创建时自定义任务标题（≤80 字；空则不下发）。 */
  title?: string;
  topic?: string;
  script?: string;
  lang?: string;
  voice_id?: string;
  aspect?: "9:16" | "16:9" | "1:1";
  style?: string;
  n_scenes?: number;
  // C7：follow_project = 绑项目任务表单「跟随项目」哨兵（后端按未显式处理）
  subtitle_style?:
    | "default"
    | "bold"
    | "minimal"
    | "outline"
    | "none"
    | "follow_project";
  transition?:
    | "none"
    | "fade"
    | "dissolve"
    | "slide_left"
    | "slide_right"
    | "wipe_left"
    | "wipe_right"
    | "zoom_in";
  transition_duration?: number;
  bgm?: string | null;
  template_id?: string;
  template_params?: Record<string, unknown>;
  output_format?: "mp4" | "webm" | "gif";
  quality?: "draft" | "standard" | "high" | "preview";
  tts_speed?: number;
  bgm_volume?: number;
  bgm_ducking?: boolean;
  engine?: string;
  /**
   * Task 6（2026-08-01）：任务级生成模型。不传 = 跟随后端 config 默认。
   * 显式指定则后端**不降级**（选了 A 却静默出 B 比失败更糟）。
   */
  image_provider?: string | null;
  video_provider?: string | null;
  auto_render?: boolean;
  target_duration?: number;
  /** 每图时长（秒，3-8）：用户自选帧密度，胜过 8s 下限。 */
  seconds_per_frame?: number;
  /** C1: 上传文档默认 LLM 改写成干净旁白（剥标题/页码/项目符号）。默认 true。 */
  ai_normalize_script?: boolean;
  course_project_id?: string;
  /**
   * T3（2026-09-01）：任务级图文镜占比**上限**（%）。不传 = 默认 30%。
   * 上限≠保障：实际占比由课程内容决定（调度表全片规划），不会超过所选值。
   */
  graphic_ratio?: 30 | 40 | 50 | 70;
  /**
   * remotion_course（2026-09-10 独立管线计划 T7）：解析双档——faithful 保真
   * （默认）/ rewrite AI 改写。仅 remotion_course 消费。
   */
  course_parse_mode?: "faithful" | "rewrite";
  /** docx 图片暂存 extract_id（extract?with_images=1 返回）；仅 remotion_course 消费。 */
  script_extract_id?: string | null;
}

/** Storyboard frame for draft editing. */
export interface StoryboardFrameData {
  index: number;
  narration: string;
  image_prompt?: string | null;
  search_term?: string | null;
  audio_path?: string | null;
  duration?: number;
  visual_url?: string | null;
  local_visual_path?: string | null;
  visual_type?: string | null;
  visual_source?: string | null;
  /** T7（2026-09-08 remotion_course）：版式指派（19 版式 PascalCase；null = 确定性规则）。 */
  layout?: string | null;
  /**
   * remotion_course（2026-09-10 独立管线计划 T1）：版式结构化内容（SceneContent
   * 子集；编辑 schema 见 layout-content-schema.ts）。null = 走确定性推导。
   * 永不承载图片数据——图槽素材走 local_visual_path / visual_url。
   */
  layout_content?: Record<string, unknown> | null;
  /**
   * remotion_course（§3.6 一单元多图顺延）：配图来自邻近单元的顺延标注
   * （"来自邻近单元的文档配图"）——素材区展示，供用户纠正。仅展示用途。
   */
  image_note?: string | null;
  /**
   * content-match（2026-08-14）：graphic 帧的结构化渲染数据（gateway Pillow 确定性
   * 渲染输入）。v1 只读（D6：用户编辑 v2 再开放）；null/缺省 = 无 spec（渲染期
   * text_card(旁白) 保底）。字段随 spec 类型变化，此处只声明展示所需的最小形状。
   */
  graphic_spec?: GraphicSpec | null;
  srt?: string;
  preview_error?: string | null;
  /** 单帧预览状态（2026-09-07 状态机）：running=在途，done=成功，error=失败（preview_error 附消息）。 */
  preview_state?: "running" | "done" | "error";
  /** 单帧重生状态（202 异步化，Task 4）：running=在途，done=成功，error=失败（regen_error 附消息）。 */
  regen_state?: "running" | "done" | "error";
  regen_error?: string | null;
  /** 重生状态时间戳（条目 35，ISO UTC）——批量重生监看用它判代（防复用上一轮旧 done）。 */
  regen_state_at?: string;
}

/** graphic spec 最小展示形状（只读摘要用；完整 schema 在后端 graphic_spec.py）。 */
export interface GraphicSpec {
  type?: "text_card" | "big_number" | "steps";
  title?: string;
  number?: string;
  unit?: string;
  description?: string;
  points?: string[];
  steps?: string[];
}

/** Storyboard config snapshot. */
export interface StoryboardConfigData {
  video_type: string;
  width: number;
  height: number;
  fps?: number;
  voice_id?: string;
  tts_speed?: number;
  style?: string | null;
  subtitle_style?: string;
  transition?: string;
  transition_duration?: number;
  bgm_volume?: number;
}

/** S6（2026-08-26 调度表计划）：全片镜头调度表的一行（帧序号绑定，随 storyboard.json 下发）。 */
export interface ShotDirectionData {
  frame_index: number;
  shot_size: string; // wide|medium|close
  camera_move: string; // static|push_in|pull_out|pan|tilt
  subject_position?: string;
  composition?: string;
  continuity?: string;
}

/** Full storyboard for draft preview. */
export interface StoryboardData {
  title: string;
  config: StoryboardConfigData;
  frames: StoryboardFrameData[];
  scene_groups?: SceneGroupData[];
  total_duration: number;
  audio_ready?: boolean;
  audio_error?: string;
  /** Plan A（2026-10-18）：同轮次失败阶段（tts|concat|subtitle|merge） */
  audio_error_stage?: string;
  /** Plan B（2026-10-19）：成功降级提示——只由「实际音色已归位」sidecar 记录生成；
   * audioReady 时由 AudioPreviewBar 琥珀条展示（请求 X，实际 Y）。 */
  audio_degradation_warning?: string | null;
  /** 用户手改字幕置锁（T15）：recompose 用用户 SRT；旁白变更清锁重新生成。 */
  subtitle_locked?: boolean;
  /** M3-3a: task status (draft/ready/composing/...) — backend injects for composing-visibility. */
  status?: string;
  progress?: number;
  /** S6：镜头调度表（S1 随 storyboard.json 持久化；无键 = 存量任务/未开启/降级——零噪音不显示）。 */
  shot_table?: ShotDirectionData[];
}

/** Video task with full detail (payload/result). */
export interface VideoTaskDetail extends VideoTaskBrief {
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
}

/** 2026-10-20 Task 7：ViMax 只读费用/成片时长预估（后端 /cost-estimate）。 */
export interface VideoCostEstimate {
  currency: string;
  low_cny: number | null;
  high_cny: number | null;
  point_cny: number | null;
  basis: "history" | "price_table" | "partial";
  n_shots: number;
  video_seconds: number;
  n_characters: number;
  breakdown: Record<string, number | null>;
  unpriced: string[];
  spent_cny: number | null;
}

/** TTS voice info. */
export interface VoiceInfo {
  id: string;
  name: string;
  locale: string;
  gender: string;
  provider?: string;
  preview_url?: string | null;
}

/** Language option. */
export interface LangOption {
  value: string;
  label: string;
  defaultVoiceId: string;
}

/** Available languages with labels and default voice mapping. */
export const LANG_OPTIONS: LangOption[] = [
  { value: "zh", label: "中文", defaultVoiceId: "zh-CN-YunjianNeural" },
  { value: "en", label: "English", defaultVoiceId: "en-US-ChristopherNeural" },
  { value: "ja", label: "日本語", defaultVoiceId: "ja-JP-KeitaNeural" },
  { value: "ko", label: "한국어", defaultVoiceId: "ko-KR-HyunsuNeural" },
  { value: "fr", label: "Français", defaultVoiceId: "fr-FR-HenriNeural" },
  { value: "de", label: "Deutsch", defaultVoiceId: "de-DE-ConradNeural" },
  { value: "es", label: "Español", defaultVoiceId: "es-ES-AlvaroNeural" },
];

/** BGM track info. */
export interface BGMTrack {
  id: string;
  name: string;
  preview_url: string;
  source: "built-in" | "custom";
  tags?: string[];
  duration?: number | null;
}

/** BGM list response from backend. */
export interface BGMListResponse {
  built_in: BGMTrack[];
  custom: BGMTrack[];
}

/** BGM upload response from backend. */
export interface BGMUploadResponse {
  id: string;
  name: string;
  preview_url: string;
}

// ── Scene Groups ──────────────────────────────────────────────────────────

/** A scene group (paragraph) in the storyboard. */
export interface SceneGroupData {
  index: number;
  title: string;
  frame_indices: number[];
  visual_theme?: string | null;
  visual_source_override?: string | null;
  transition_in?: string;
  transition_out?: string;
  transition_duration?: number;
}

// ── Course Projects ────────────────────────────────────────────────────────

/** A course project for visual consistency across videos. */
export interface CourseProject {
  id: string;
  user_id: string;
  title: string;
  visual_style_preset?: string | null;
  color_palette?: Record<string, string | number | undefined> | null;
  character_reference?: string | null;
  /** Task 9: 角色三视图（front/side/back + features），跨视频角色一致 */
  character_sheets?: CharacterSheetData[];
  /** C1: 风格锚图本地绝对路径（None = 未设置） */
  style_anchor_image?: string | null;
  /** T5(2026-08-31): 未固化候选文件名（style_anchor/{pid}/candidates/ 下）；
   * 生成成功落字段、固化/删除/换一张/生成失败清空——挂载非空自动恢复候选条 */
  style_anchor_candidate?: string | null;
  /** 决策 21: 项目级默认音色（"" = 跟随系统默认，未设置） */
  voice_id?: string;
  /** T-E11（D6）: 项目级默认语速（null = 未设置走任务缺省/config） */
  tts_speed?: number | null;
  /** 决策 21: 项目级默认字幕样式（"default" = 未设置） */
  subtitle_style?: string;
  /** M6: 道具资产（主图 1 + 补角度 0~2，仅手动上传） */
  prop_assets?: PropAssetData[];
  /** T8/T9（2026-09-08 remotion-course）：品牌包 A 类字段 */
  remotion_theme?: string | null;
  font_family?: string | null;
  logo_path?: string | null;
  intro_outro?: {
    intro?: { headline?: string; subtitle?: string };
    outro?: { headline?: string; follow?: string };
  } | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** M6: 道具资产（决策 12/15/16/18） */
export interface PropAssetData {
  id: string;
  name: string;
  main_image: string;
  extra_images?: string[];
  description?: string;
}

/** Task 9: 角色三视图资产（M1: id 供管理端点寻址；帧级引用键仍是 name） */
export interface CharacterSheetData {
  id?: string;
  name?: string;
  front_url: string;
  side_url: string;
  back_url: string;
  /** 三视图合成图（2026-08-27 计划 X1）：注入通道恒带；资产页缩略图展示 */
  composite_url?: string;
  features?: string;
}

/** M5: 参考注入留痕条目（task_dir/sidecar/ref_injection.json，任务详情响应并入） */
export interface RefInjectionEntry {
  entity: string;
  path?: string;
  view?: string | number;
  reason?: string;
}

/** M5: 帧级参考注入留痕（决策 19——分镜级「参考资产」标签数据源） */
export interface RefInjectionRecord {
  frame_index: number;
  declared_entities?: string[];
  injected_refs?: RefInjectionEntry[];
  truncated?: RefInjectionEntry[];
  skipped?: RefInjectionEntry[];
  reason?: string;
}

/** Create/update course project request body. */
export interface CourseProjectInput {
  title: string;
  visual_style_preset?: string;
  color_palette?: Record<string, string | number | undefined>;
  character_reference?: string;
  /** C1: 风格锚图本地绝对路径（显式 "" = 清空——PUT exclude_unset 语义） */
  style_anchor_image?: string | null;
  /** 决策 21: 项目级默认音色（显式 "" = 清空=跟随系统默认） */
  voice_id?: string;
  /** T-E11（D6）: 项目级默认语速（显式 null = 清空=跟随任务缺省/config） */
  tts_speed?: number | null;
  /** 决策 21: 项目级默认字幕样式（显式 "" = 清空=回落 default） */
  subtitle_style?: string;
  /** M6: 道具资产（主图 1 + 补角度 0~2，仅手动上传） */
  prop_assets?: PropAssetData[];
  /** T8/T9：品牌包字段（显式 "" / {} = 清空回落默认） */
  remotion_theme?: string;
  font_family?: string;
  intro_outro?: {
    intro?: { headline?: string; subtitle?: string };
    outro?: { headline?: string; follow?: string };
  };
}

// ── SSE Progressive Preview ──────────────────────────────────────────────────

/** A frame preview pushed via SSE during visual generation. */
export interface PreviewFrame {
  frame_index: number;
  visual_url: string;
  visual_type: string;
  progress: number;
}

/** 视频类型（与后端 video_type 对齐；ai_visual 是历史别名） */
export type VideoTaskType =
  | "ai_image"
  | "ai_video"
  | "ai_visual"
  | "remotion"
  | "remotion_course"
  | "vimax";

/** E5（2026-08-21）：能力/风险标注元数据（后端静态表下发；D5 口径：
 *  实测结论 / 「未检测」，不推断）。 */
export interface ProviderAnnotations {
  /** 是否消费参考图（i2i / r2v） */
  reference_images: boolean;
  /** 实测状态文案；未实测 = 「未检测」 */
  tested: string;
  /** 提示文案（官方文档口径事实，如「仅 480p/720p，低成本」）；可为空串 */
  notes: string;
}

/** GET /api/video/providers 的单项（Task 6） */
export interface ProviderInfo {
  name: string;
  label: string;
  /** key 是否已配置；false 的项不该出现在下拉里 */
  available: boolean;
  /** 是否为 config.yaml 里的默认模型 */
  is_default: boolean;
  /** E5（2026-08-21）：能力/风险标注；旧后端无此键（可选） */
  annotations?: ProviderAnnotations;
}

/** GET /api/video/providers 响应 */
export interface ProviderListResponse {
  image: ProviderInfo[];
  video: ProviderInfo[];
}

/**
 * T7：remotion_course 19 版式全集（与后端 storyboard.REMOTION_LAYOUTS 同源）。
 * label 为中文短名（LayoutSwitcher 下拉 + M0 示意图文件名映射）。
 */
export const REMOTION_LAYOUTS: ReadonlyArray<{ value: string; label: string }> =
  [
    { value: "CoverPage", label: "课程封面" },
    { value: "ObjectivesPage", label: "学习目标" },
    { value: "OutlinePage", label: "课程大纲" },
    { value: "ChapterDivider", label: "章节过渡" },
    { value: "ConceptPage", label: "概念要点" },
    { value: "ImageTextLeft", label: "图文（左图）" },
    { value: "ImageTextRight", label: "图文（右图）" },
    { value: "FullVisualPage", label: "全幅视觉" },
    { value: "VideoClipPage", label: "视频片段" },
    { value: "ComparisonPage", label: "对比页" },
    { value: "ChartPage", label: "数据图表" },
    { value: "ProcessPage", label: "流程步骤" },
    { value: "CodePage", label: "代码示例" },
    { value: "QuotePage", label: "金句强调" },
    { value: "CasePage", label: "案例页" },
    { value: "SummaryPage", label: "小结页" },
    { value: "QuizPage", label: "思考题" },
    { value: "EndingPage", label: "结尾行动" },
    { value: "EndCard", label: "片尾" },
  ];

/**
 * PascalCase 版式 id → M0 示意图文件名（D15 降级用）。
 * T-E7（R22，2026-09-09）：四主题预览图收录至 /remotion-layouts/<theme>/（kebab
 * 命名与版式 id 映射写死一种）；theme 缺省回落根目录 19 张（dark-blue，兼容）。
 */
export function layoutFallbackImage(
  layout: string | null | undefined,
  theme?: string | null,
): string {
  const kebab = (layout ?? "ConceptPage")
    .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
    .toLowerCase();
  const themeSeg =
    theme && ["dark-blue", "light", "dark-green", "warm"].includes(theme)
      ? `${theme}/`
      : "";
  return `/remotion-layouts/${themeSeg}${kebab}.png`;
}
