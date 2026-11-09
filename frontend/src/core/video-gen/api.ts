/**
 * Video generation API client.
 */

import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";
import { getBackendBaseURL } from "@/core/config";

import type {
  BGMListResponse,
  ProviderListResponse,
  BGMUploadResponse,
  CourseProject,
  CourseProjectInput,
  CreateVideoRequest,
  StoryboardData,
  VideoTaskBrief,
  VideoTaskDetail,
  VideoCostEstimate,
  VoiceInfo,
} from "./types";

// ── Helpers ──────────────────────────────────────────────────────────────

/**
 * P2-4 / BUG-007（2026-09-14）：带 HTTP status 的 API 错误（对齐 writer 模块
 * WriterDocConflictError 模式）。修前 readErrorDetail 只回传 message string、
 * 丢弃 status，组件只能靠文案匹配判定 409——路由透传具体文案后该匹配更脆弱。
 */
export class VideoApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "VideoApiError";
    this.status = status;
  }
}

const BASE = `${getBackendBaseURL()}/api/video`;

// ── Task listing ─────────────────────────────────────────────────────────

/** List all video tasks for the current user. */
export async function listVideoTasks(): Promise<VideoTaskBrief[]> {
  const res = await fetch(`${BASE}/tasks`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to list video tasks",
    );
    throw new Error(detail);
  }
  const tasks = (await res.json()) as VideoTaskBrief[];
  // T-E10（M5）：still 预览任务非用户任务——后端已过滤，前端兜底（旧网关
  // 混布窗口/缓存响应不漏 [still] 行）。
  return tasks.filter((t) => (t.task_type as string) !== "remotion_still");
}

/** Get a single video task. */
export async function getVideoTask(taskId: string): Promise<VideoTaskDetail> {
  const res = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to get video task",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** 2026-10-20 Task 7：ViMax 费用/成片时长预估（只读，不触发 provider）。 */
export async function getCostEstimate(
  taskId: string,
): Promise<VideoCostEstimate> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/cost-estimate`,
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to estimate video cost",
    );
    throw new VideoApiError(detail, res.status);
  }
  return res.json();
}

/** Delete a video task. */
export async function deleteVideoTask(taskId: string): Promise<void> {
  const res = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to delete video task",
    );
    throw new Error(detail);
  }
}

// ── Task creation ────────────────────────────────────────────────────────

/** Submit a video task via the unified /create endpoint. Returns array of tasks. */
export async function submitVideo(
  req: CreateVideoRequest,
): Promise<VideoTaskBrief[]> {
  const res = await fetch(`${BASE}/create`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to submit video task",
    );
    throw new Error(detail);
  }
  return res.json();
}

// ── Download ─────────────────────────────────────────────────────────────

/** Get the video download URL for a completed task. */
export function getVideoDownloadUrl(taskId: string): string {
  return `${getBackendBaseURL()}/api/video/tasks/${encodeURIComponent(taskId)}/download`;
}

// ── Storyboard (draft) ────────────────────────────────────────────────

/** Get storyboard data for a draft task. */
export async function getStoryboard(taskId: string): Promise<StoryboardData> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/storyboard`,
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to get storyboard",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** Update a single frame in the storyboard. */
export async function updateStoryboardFrame(
  taskId: string,
  frameIndex: number,
  updates: {
    narration?: string;
    image_prompt?: string;
    search_term?: string;
    /** T7（2026-09-10 独立管线计划）：remotion_course 版式结构化内容（null = 清除）。 */
    layout_content?: Record<string, unknown> | null;
    /** T7：remotion_course 用户显式视觉来源（ai_image/ai_video；ai_video 仅 VideoClipPage）。 */
    visual_source?: string | null;
  },
): Promise<{
  storyboard: StoryboardData;
  updated: Record<string, string>;
  warning?: string;
}> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/storyboard/frames/${frameIndex}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to update frame");
    throw new Error(detail);
  }
  return res.json();
}

/** Start rendering from a draft task. */
export async function renderFromDraft(
  taskId: string,
  frameEdits?: Record<string, Record<string, string>>,
  quality?: "draft" | "standard" | "high" | "preview",
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/render`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frame_edits: frameEdits ?? {}, quality }),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to start rendering",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** Edit the expanded story for an idea2video task (story_ready status). */
export async function updateStory(
  taskId: string,
  story: string,
): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}/story`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ story }),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to update story");
    throw new Error(detail);
  }
  return res.json();
}

/** Regenerate visual for a single frame in a completed task.
 * mode（2026-07-27 音视频分离）：all（默认，画面+音频）/ audio_only（只重配音）/ visual_only（只重画面）。 */
export async function regenerateFrame(
  taskId: string,
  frameIndex: number,
  mode: "all" | "audio_only" | "visual_only" = "all",
): Promise<{ frame_index: number; storyboard: StoryboardData }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/frames/${frameIndex}/regenerate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to regenerate frame",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** Update user-tunable storyboard config fields（2026-07-27：voice/speed/bgm_volume/subtitle_style）。
 * 生效路径：voice_id/tts_speed → 逐帧「重生配音」或整片 re-render；bgm_volume/subtitle_style → 「重新合成」即生效。 */
export async function updateStoryboardConfig(
  taskId: string,
  patch: {
    // F4（2026-09-28）：三分态——未动=键不发；「默认/跟随项目」=显式 null；选定=值
    voice_id?: string | null;
    tts_speed?: number;
    bgm_volume?: number;
    subtitle_style?: string;
  },
): Promise<{
  updated: Record<string, unknown>;
  config: Record<string, unknown>;
}> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/storyboard/config`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to update storyboard config",
    );
    throw new Error(detail);
  }
  return res.json();
}

// ── Subtitle editing（2026-07-27 T15：字幕可编辑 + subtitle_locked） ─────────

export interface SubtitleCueData {
  seq?: number;
  start: string;
  end: string;
  text: string;
}

/** 读取任务的 SRT 字幕 cue + 用户锁定状态。 */
export async function getSubtitle(
  taskId: string,
): Promise<{ cues: SubtitleCueData[]; locked: boolean }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/subtitle`,
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to load subtitle");
    throw new Error(detail);
  }
  return res.json();
}

/** 保存用户手改字幕——置 subtitle_locked；旁白变更才清锁按新旁白重新生成。 */
export async function updateSubtitle(
  taskId: string,
  cues: Array<{ start: string; end: string; text: string }>,
): Promise<{
  locked: boolean;
  cues: SubtitleCueData[];
  /** 该任务原设「无字幕」时，手改字幕会自动恢复为此样式（2026-07-29 死锁修复）。 */
  subtitle_style_restored?: string | null;
}> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/subtitle`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cues }),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to update subtitle",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** Generate a preview image for a single draft frame — 异步：202 started，结果经 storyboard 轮询下发（visual_url / preview_error）。 */
export async function previewFrame(
  taskId: string,
  frameIndex: number,
): Promise<{ status: string }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/frames/${frameIndex}/preview`,
    { method: "POST" },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to preview frame");
    throw new Error(detail);
  }
  return res.json();
}

/** M1-2: trigger bulk preview generation for all frames lacking visual_url. */
export async function previewAll(
  taskId: string,
): Promise<{ status: string; count: number }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/preview-all`,
    { method: "POST" },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to start bulk preview",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** M-UX: add a storyboard frame (append, or insert at index). */
export async function addFrame(
  taskId: string,
  body: { narration?: string; image_prompt?: string; index?: number },
): Promise<{ status: string }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/storyboard/frames`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to add frame");
    throw new VideoApiError(detail, res.status);
  }
  return res.json();
}

/** M-UX: delete a storyboard frame. */
export async function deleteFrame(
  taskId: string,
  index: number,
): Promise<{ status: string }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/storyboard/frames/${index}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to delete frame");
    throw new VideoApiError(detail, res.status);
  }
  return res.json();
}

// ── Recompose (batch re-render) ───────────────────────────────────────

/** D3: regenerate a single frame's image_prompt/search_term via LLM. */
export async function regenerateFramePrompt(
  taskId: string,
  frameIndex: number,
): Promise<void> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/storyboard/frames/${frameIndex}/regenerate_prompt`,
    { method: "POST" },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to regenerate prompt",
    );
    throw new Error(detail);
  }
}

export async function recomposeTask(
  taskId: string,
): Promise<{ task_id: string; status: string }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/recompose`,
    {
      method: "POST",
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to recompose task",
    );
    throw new VideoApiError(detail, res.status);
  }
  return res.json();
}

// B4（2026-09-28 edits 修复批）：批量重生全部配音（ready/failed；202 异步）
export async function regenerateAllAudio(
  taskId: string,
): Promise<{ status: string; frames: number[] }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/regenerate-audio`,
    { method: "POST" },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to regenerate audio",
    );
    throw new VideoApiError(detail, res.status);
  }
  return res.json();
}

// Task 20c: 项目已更新 → 重新生成视觉（复用分镜重跑视觉 + force 刷新项目 refs）
export async function reRenderTask(
  taskId: string,
): Promise<{ task_id: string; status: string }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/re-render`,
    {
      method: "POST",
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to re-render task",
    );
    throw new VideoApiError(detail, res.status);
  }
  return res.json();
}

export function getFrameVisualUrl(
  taskId: string,
  frameIndex: number,
  version?: number,
): string {
  const base = `${BASE}/tasks/${encodeURIComponent(taskId)}/frames/${frameIndex}/visual`;
  // version：重生/上传成功后传 Date.now() 破浏览器缓存（URL 不变时 <img> 不重取）
  return version ? `${base}?v=${version}` : base;
}

// ── Voices ──────────────────────────────────────────────────────────────

/** List available TTS voices. */
export async function listVideoVoices(lang?: string): Promise<VoiceInfo[]> {
  const url = lang
    ? `${BASE}/voices?lang=${encodeURIComponent(lang)}`
    : `${BASE}/voices`;
  const res = await fetch(url);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to list voices");
    throw new Error(detail);
  }
  return res.json();
}

/**
 * FP1-5: 后端解析的默认音色（doubao:<speaker> 或 preset:146），与成品一致——
 * 修前「默认」试听恒用 types.ts LANG_OPTIONS 的静态 edge-tts 值，
 * 试听满意≠成品声音。接口不可达时调用方退化到静态兜底值。
 */
export async function fetchDefaultVoice(
  lang: string,
  taskType?: string,
): Promise<string> {
  // Plan B（2026-10-19）：taskType=vimax 走 ViMax 策略解析（zh 兜底不再是
  // preset:146）；缺省不追加参数——旧调用方 URL/响应零变化。
  const taskTypeQuery = taskType
    ? `&task_type=${encodeURIComponent(taskType)}`
    : "";
  const res = await fetch(
    `${getBackendBaseURL()}/api/video/voices/default?lang=${encodeURIComponent(lang)}${taskTypeQuery}`,
    { credentials: "include" },
  );
  if (!res.ok) throw new Error(`获取默认音色失败（HTTP ${res.status}）`);
  return (await res.json()).voice_id as string;
}

/** Reorder frames in the storyboard (drag-and-drop). */
export async function reorderFrames(
  taskId: string,
  fromIndex: number,
  toIndex: number,
): Promise<{ storyboard: StoryboardData }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/storyboard/reorder`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_index: fromIndex, to_index: toIndex }),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to reorder frames",
    );
    throw new Error(detail);
  }
  return res.json();
}

// ── Providers（生成模型，Task 6） ─────────────────────────────────────────

/** 列出可选生成模型及可用性（可用性 = key 是否配置，不发上游请求）。 */
export async function listProviders(): Promise<ProviderListResponse> {
  const res = await fetch(`${BASE}/providers`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to list providers",
    );
    throw new Error(detail);
  }
  return res.json();
}

// ── BGM ──────────────────────────────────────────────────────────────────

/** List built-in + user-uploaded BGM tracks. */
export async function listBGM(): Promise<BGMListResponse> {
  const res = await fetch(`${BASE}/bgm`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to list BGM");
    throw new Error(detail);
  }
  return res.json();
}

/** Upload a custom BGM file. */
export async function uploadBGM(file: File): Promise<BGMUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/bgm/upload`, { method: "POST", body: form });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to upload BGM");
    throw new Error(detail);
  }
  return res.json();
}

/** T-E1（R15）：LOGO 上传——必须走带 CSRF 的 fetcher（修前组件裸 fetch 必 403）。 */
export async function uploadProjectLogo(
  projectId: string,
  file: File,
): Promise<{ logo_path: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`/api/video/projects/${projectId}/brand/logo`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "LOGO 上传失败");
    throw new Error(detail);
  }
  return res.json();
}

/** Extract text from an uploaded document (docx/doc/txt/md) for fixed-mode script. */
export async function extractScript(file: File): Promise<{ text: string }>;

/**
 * T6（2026-09-10 独立管线计划）：with_images=true 时 docx 走原位图片提取——
 * 返回文本（含 【图片N】 标记）+ staging 暂存信息（extract_id/expires_at/
 * ignored_images）。非 docx 时后端忽略该参数（images 为空、pdf 附提示）。
 */
export async function extractScript(
  file: File,
  withImages: boolean,
): Promise<ExtractScriptResponse>;

export async function extractScript(
  file: File,
  withImages = false,
): Promise<{ text: string } | ExtractScriptResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(
    `${BASE}/script/extract${withImages ? "?with_images=1" : ""}`,
    {
      method: "POST",
      body: form,
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to extract script",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** T6 + m16（2026-09-11 二轮审查）：extract?with_images=1 响应——images 只回
 * {ref,name} 元信息（字节版已移除：前端仅消费数量，图片本体在 staging）。 */
export interface ExtractScriptResponse {
  text: string;
  images: { ref: string; name: string }[];
  ignored_images: number;
  extract_id: string;
  expires_at: string;
  warning?: string;
}

/**
 * T8（决策 15）：以另一档重新解析脚本——仅 draft、整量覆盖重建分镜
 * （用户手改与已上传素材丢弃；嵌入图按标记从任务目录重新分配）。
 */
export async function reparseCourseStoryboard(
  taskId: string,
  courseParseMode: "faithful" | "rewrite",
): Promise<{
  ok: boolean;
  course_parse_mode: string;
  n_frames: number;
  n_groups: number;
  n_embedded_images: number;
}> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/reparse`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course_parse_mode: courseParseMode }),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "重新解析失败");
    throw new Error(detail);
  }
  return res.json();
}

// ── Course Projects ────────────────────────────────────────────────────────

/** List all course projects for the current user. */
export async function listCourseProjects(): Promise<CourseProject[]> {
  const res = await fetch(`${BASE}/projects`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to list course projects",
    );
    throw new Error(detail);
  }
  const data = await res.json();
  return data.projects ?? data;
}

/** Create a new course project. */
export async function createCourseProject(
  input: CourseProjectInput,
): Promise<CourseProject> {
  const res = await fetch(`${BASE}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to create course project",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** Get a single course project. */
export async function getCourseProject(
  projectId: string,
): Promise<CourseProject> {
  const res = await fetch(`${BASE}/projects/${encodeURIComponent(projectId)}`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to get course project",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** Update a course project. */
export async function updateCourseProject(
  projectId: string,
  input: Partial<CourseProjectInput>,
): Promise<CourseProject> {
  const res = await fetch(`${BASE}/projects/${encodeURIComponent(projectId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to update course project",
    );
    throw new Error(detail);
  }
  return res.json();
}

// ── C6（决策 4）：风格锚图管理——AI 生成 + 手动上传双入口 ────────────────────

/** AI 生成风格样图（落盘 candidates/，不写项目字段；失败可重试、重生不限次）。
 *  includePeople（方案 B，默认 false）：勾选后允许画面出现人物（预设原样）；
 *  不勾选时后端剥离预设人物词并追加无人物硬约束。 */
export async function generateStyleAnchorImage(
  projectId: string,
  prompt: string,
  includePeople: boolean,
): Promise<{ image_url: string; candidate_path: string }> {
  const res = await fetch(
    `${BASE}/projects/${encodeURIComponent(projectId)}/style-anchor-image/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, include_people: includePeople }),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "风格图生成失败");
    throw new Error(detail);
  }
  return res.json();
}

/** 固化候选（candidate_path / candidate 文件名）或上传图片（file）→ 写项目
 * style_anchor_image（替换删旧文件）。
 *
 * T5（2026-08-31 候选持久化）：`candidate`（文件名）用于挂载恢复后的固化——
 * 恢复态只从项目字段拿到文件名（无绝对路径），后端按名解析到 candidates/ 下。 */
export async function setStyleAnchorImage(
  projectId: string,
  body: { candidate_path: string } | { candidate: string } | { file: File },
): Promise<CourseProject> {
  const init: RequestInit =
    "file" in body
      ? {
          method: "POST",
          body: (() => {
            const fd = new FormData();
            fd.append("file", body.file);
            return fd;
          })(),
        }
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            "candidate_path" in body
              ? { candidate_path: body.candidate_path }
              : { candidate: body.candidate },
          ),
        };
  const res = await fetch(
    `${BASE}/projects/${encodeURIComponent(projectId)}/style-anchor-image`,
    init,
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "设置风格锚图失败");
    throw new Error(detail);
  }
  return res.json();
}

/** serve URL：当前锚图（无 candidate）或候选预览（生成对话框用）。 */
export function styleAnchorImageUrl(
  projectId: string,
  candidate?: string,
): string {
  const q = candidate ? `?candidate=${encodeURIComponent(candidate)}` : "";
  return `${BASE}/projects/${encodeURIComponent(projectId)}/style-anchor-image${q}`;
}

/** 删除锚图（清字段 + 删文件）。 */
export async function deleteStyleAnchorImage(
  projectId: string,
): Promise<CourseProject> {
  const res = await fetch(
    `${BASE}/projects/${encodeURIComponent(projectId)}/style-anchor-image`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "删除风格锚图失败");
    throw new Error(detail);
  }
  return res.json();
}

/** T5（2026-08-31 候选持久化）：弃用未固化候选（「换一张」）——清字段 + 删候选
 * 文件（幂等）。防脏恢复：只清本地 state 不清字段，退出重进会复活已弃候选。 */
export async function clearStyleAnchorCandidate(
  projectId: string,
): Promise<CourseProject> {
  const res = await fetch(
    `${BASE}/projects/${encodeURIComponent(projectId)}/style-anchor-candidate`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "弃用候选失败");
    throw new Error(detail);
  }
  return res.json();
}

/** Delete a course project. */
export async function deleteCourseProject(projectId: string): Promise<void> {
  const res = await fetch(`${BASE}/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to delete course project",
    );
    throw new Error(detail);
  }
}

// ── Draft Audio ────────────────────────────────────────────────────────────

/** Manually trigger TTS audio generation for a draft task. */
export async function generateDraftAudio(
  taskId: string,
): Promise<{ status: string; total_duration?: number }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/generate-audio`,
    {
      method: "POST",
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to generate audio",
    );
    throw new Error(detail);
  }
  return res.json();
}

// ── Frame visual source ──────────────────────────────────────────────────

/** Upload a local image/video to replace a frame's visual source. */
export async function uploadFrameVisual(
  taskId: string,
  frameIndex: number,
  file: File,
): Promise<{ ok: boolean; path: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/frames/${frameIndex}/upload-visual`,
    { method: "POST", body: form },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to upload frame visual",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** T7：写回帧版式（remotion_course 19 版式；null 值由后端走确定性规则）。 */
export async function updateFrameLayout(
  taskId: string,
  frameIndex: number,
  layout: string | null,
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/storyboard/frames/${frameIndex}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layout }),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to update frame layout",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** T7：发起静帧预览（202；缓存命中 status=cached）。
 *
 * T-E9（v4.2）：`bulk=true`（帧条批量缩略图）→ 后端 still 任务 priority=10，
 * 不堵交互预览(20)/成片渲染(35)；交互预览不传（默认 20）。
 */
export async function stillPreview(
  taskId: string,
  frameIndex: number,
  bulk?: boolean,
): Promise<{ status: string; cache_key: string }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/frames/${frameIndex}/still-preview${bulk ? "?bulk=true" : ""}`,
    { method: "POST" },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to start still preview",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** T7：轮询静帧预览——ready 时返回 PNG blob URL；running/failed 返回状态。 */
export async function fetchStillPreview(
  taskId: string,
  frameIndex: number,
  cacheKey: string,
): Promise<{ status: "ready" | "running" | "failed"; url?: string }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/frames/${frameIndex}/still-preview?cache_key=${encodeURIComponent(cacheKey)}`,
  );
  if (!res.ok) return { status: "failed" };
  const ct = res.headers.get("content-type") ?? "";
  if (ct.startsWith("image/")) {
    const blob = await res.blob();
    return { status: "ready", url: URL.createObjectURL(blob) };
  }
  const body = (await res.json().catch(() => ({}))) as { status?: string };
  return { status: body.status === "running" ? "running" : "failed" };
}

/** Update a frame's visual_source type (for mixed visual sources). */
export async function updateFrameVisualSource(
  taskId: string,
  frameIndex: number,
  visualSource: string,
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/storyboard/frames/${frameIndex}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visual_source: visualSource }),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to update frame visual source",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** W2（2026-08-17）：任务级一键关闭图文卡——全部 graphic 帧回主引擎（graphic_spec 保留）。 */
export async function bulkDisableGraphic(
  taskId: string,
): Promise<{ disabled: number }> {
  const res = await fetch(
    `${BASE}/tasks/${encodeURIComponent(taskId)}/storyboard/visual-source/bulk`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "disable_graphic" }),
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to disable graphic frames",
    );
    throw new Error(detail);
  }
  return res.json();
}
