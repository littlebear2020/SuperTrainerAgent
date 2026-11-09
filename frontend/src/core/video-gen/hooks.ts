/**
 * Video generation React Query hooks.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createElement, useEffect, useState } from "react";
import { toast } from "sonner";

import { fetch } from "@/core/api/fetcher";
import { formatFastApiErrorDetail } from "@/core/api/read-fastapi-error";
import { getBackendBaseURL } from "@/core/config";

import {
  updateFrameLayout,
  createCourseProject,
  deleteVideoTask,
  deleteCourseProject,
  extractScript,
  fetchDefaultVoice,
  generateDraftAudio,
  getCourseProject,
  getCostEstimate,
  getStoryboard,
  getSubtitle,
  getVideoTask,
  listBGM,
  listProviders,
  listCourseProjects,
  listVideoTasks,
  listVideoVoices,
  previewFrame,
  previewAll,
  addFrame,
  deleteFrame,
  regenerateFrame,
  regenerateFramePrompt,
  regenerateAllAudio,
  recomposeTask,
  reRenderTask,
  VideoApiError,
  renderFromDraft,
  updateStory,
  reorderFrames,
  submitVideo,
  deleteStyleAnchorImage,
  generateStyleAnchorImage,
  setStyleAnchorImage,
  clearStyleAnchorCandidate,
  updateCourseProject,
  updateFrameVisualSource,
  bulkDisableGraphic,
  updateStoryboardConfig,
  updateStoryboardFrame,
  updateSubtitle,
  uploadBGM,
  uploadFrameVisual,
} from "./api";
import { MAX_SSE_RETRIES, nextBackoffMs } from "./sse-backoff";
import type {
  CourseProjectInput,
  CreateVideoRequest,
  PreviewFrame,
  StoryboardData,
  VideoTaskBrief,
} from "./types";

// ── Query keys ───────────────────────────────────────────────────────────

const VIDEO_KEY = "videoTasks";
const COST_ESTIMATE_KEY = "videoCostEstimate";

// ── Stall detection ──────────────────────────────────────────────────────

/** E8: pending/running/composing 超过此时长无更新 → 提示"处理时间过长"。 */
const STALL_THRESHOLD_MS = 5 * 60_000;

// T-C2（R14）：qc_repair 纳入停滞检测（watcher 重生卡死超阈值同样须提示）
const STALLABLE_STATUSES = new Set([
  "pending",
  "running",
  "composing",
  "qc_repair",
]);

/**
 * E8: 判断任务是否"卡死"（pending/running/composing 长时间无更新）。
 *
 * 优先用 updated_at（最接近"上次进展"时刻，最准）；缺失时用 created_at 粗代理
 * （注意：created_at 是提交时间，排队久的任务会过度报警，仅作兜底）。返回降级提示文案，
 * 未超阈值或已终态返回 null。纯函数（now 可注入）便于测试。**不停轮询**，仅加 UI 提示。
 */
export function getStallWarning(
  task: Pick<VideoTaskBrief, "status" | "updated_at" | "created_at">,
  now: number = Date.now(),
): string | null {
  if (!STALLABLE_STATUSES.has(task.status)) return null;
  const ts = task.updated_at ?? task.created_at;
  if (!ts) return null;
  const elapsed = now - new Date(ts).getTime();
  if (elapsed <= STALL_THRESHOLD_MS) return null;
  const mins = Math.round(elapsed / 60_000);
  return `处理时间过长（已 ${mins} 分钟无进展），可能异常。可重试或联系管理员。`;
}

// ── List tasks ───────────────────────────────────────────────────────────

export function useVideoTasks() {
  return useQuery({
    queryKey: [VIDEO_KEY],
    queryFn: listVideoTasks,
    refetchInterval: (query) => {
      const tasks = query.state.data;
      if (!tasks) return false;
      // Only poll when there are actively processing tasks.
      // "draft" is excluded — it's a persistent state and polling it
      // would cause an unbounded QPS explosion as users accumulate drafts.
      const hasActive = tasks.some(
        (t) =>
          t.status === "running" ||
          t.status === "pending" ||
          t.status === "composing" || // E16: composing 时列表须继续轮询（recompose 完成自动刷新）
          t.status === "qc_repair", // T-C2（R14）：质检修复态继续轮询（重生→ready 跳变）
      );
      return hasActive ? 5000 : false;
    },
  });
}

// ── Single task (with polling) ───────────────────────────────────────────

/**
 * useVideoTask 轮询决策（纯函数，便于单测）。
 * ready/failed 停轮询——除非 pollWhile() 为真（recompose 宽限期：派发后 status
 * 要等拿到渲染信号量才翻 composing，停轮询会错过 composing→ready 跳变，
 * 视频不刷新、dirty 徽标不清——M8）。
 */
export function videoTaskRefetchInterval(
  status: string | undefined,
  pollWhile?: () => boolean,
): number | false {
  if (status === "ready" || status === "failed") {
    return pollWhile?.() ? 2000 : false;
  }
  return 2000;
}

export function useVideoTask(
  taskId: string | undefined,
  options?: { enabled?: boolean; pollWhile?: () => boolean },
) {
  return useQuery({
    queryKey: [VIDEO_KEY, taskId],
    queryFn: () => getVideoTask(taskId!),
    // B1：默认 true（detail.tsx / confirm-card.tsx 既有调用不变）；
    // 分镜页传 { enabled: !storyboardLoaded } 在 storyboard 加载后立即停，
    // 否则 draft 持久态会每 2s 永久轮询（同 useVideoTasks 的 QPS 坑）。
    enabled: !!taskId && (options?.enabled ?? true),
    refetchInterval: (query) =>
      videoTaskRefetchInterval(query.state.data?.status, options?.pollWhile),
  });
}

/**
 * 2026-10-20 Task 7：确认渲染弹窗专用费用预估——只在弹窗打开时请求，
 * 失败静默降级（不阻塞确认渲染）；staleTime 0，每次弹窗打开都重新请求。
 */
export function useCostEstimate(
  taskId: string | undefined,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: [COST_ESTIMATE_KEY, taskId],
    queryFn: () => getCostEstimate(taskId!),
    enabled: !!taskId && (options?.enabled ?? true),
    // 弹窗打开即 refetch（分镜编辑/花费变化后不能展示 60s 旧值）。
    staleTime: 0,
    retry: false,
  });
}

// ── Submit tasks ─────────────────────────────────────────────────────────

export function useSubmitVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: CreateVideoRequest) => submitVideo(req),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY] });
    },
    // FP1-3: 创建主流程失败必发声（修前 403/422/500/网络错误全静默，
    // 用户无法区分没点上/参数错/服务器挂）。
    onError: (err) => {
      toast.error(
        err instanceof Error ? err.message : "创建视频任务失败，请重试",
      );
    },
  });
}

// ── Delete task ──────────────────────────────────────────────────────────

export function useDeleteVideoTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteVideoTask(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY] });
    },
    onError: (err) => {
      // 修前删除失败完全无提示：任务还在列表里，用户以为没点上又点一次。
      toast.error(
        `删除失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

// ── Voices ───────────────────────────────────────────────────────────────

const VOICE_KEY = "videoVoices";

export function useVideoVoices(lang?: string) {
  return useQuery({
    queryKey: [VOICE_KEY, lang],
    queryFn: () => listVideoVoices(lang),
    staleTime: 1000 * 60 * 30, // 30 min — voices rarely change
  });
}

/**
 * FP1-5: 后端解析的默认音色（doubao:<speaker>/preset:146），与成品一致。
 * 「默认」选项试听用——修前试听恒 edge-tts 静态值，试听满意≠成品声音。
 * 接口失败时 data 为 undefined，调用方退化到静态兜底值（评审预案 #8）。
 */
export function useDefaultVoice(lang: string, taskType?: string) {
  return useQuery({
    // Plan B（2026-10-19）：query key 含 taskType——vimax 与缺省默认不共用缓存
    queryKey: ["video-gen-default-voice", lang, taskType ?? null],
    queryFn: () => fetchDefaultVoice(lang, taskType),
    staleTime: 5 * 60 * 1000,
  });
}

// ── Storyboard ───────────────────────────────────────────────────────

const STORYBOARD_KEY = "videoStoryboard";

export { STORYBOARD_KEY, VIDEO_KEY };

/**
 * E1 占位响应判别：草稿生成期后端返 200 {status:"generating"}（storyboard.json
 * 尚未落盘，语义「正在生成≠不存在」）。真实 storyboard 的 status 由后端注入
 * task.status（draft/ready/composing/...），永不为 "generating"，不会误伤。
 */
export function isStoryboardGenerating(d: StoryboardData | undefined): boolean {
  return !!d && (d as { status?: string }).status === "generating";
}

/**
 * E1: 占位响应在组件层归一化为 undefined——消费方的 storyboard && 守卫恢复
 * 设计语义（修前裸 {status:"generating"} 入缓存，detail 页读 .frames.length 崩）。
 * select 只作用于渲染层；query.state.data 保持原始响应，下方 2s 轮询不受影响。
 */
const storyboardSelect = (d: StoryboardData): StoryboardData | undefined =>
  isStoryboardGenerating(d) ? undefined : d;

export function useStoryboard(taskId: string | undefined) {
  return useQuery({
    queryKey: [STORYBOARD_KEY, taskId],
    queryFn: () => getStoryboard(taskId!),
    enabled: !!taskId,
    // M3-3a: composing 期后端已改 200 返回磁盘 storyboard（不再 409）；此项为兜底，
    // 防止 confirm-card 挂载时对偶发 409 走 RQ 默认 3 重试→4× 突发。
    retry: false,
    select: storyboardSelect,
  });
}

/**
 * 决定分镜轮询间隔（纯函数，便于单测）。
 *
 * 边界 B2：taskFailed 时返回 false——task 已死，storyboard 永不落盘，不能无限轮询 404。
 * - storyboard.json 未落盘（getStoryboard 抛 404 → query.error）、未失败 → 每 2s 轮询
 *   （T1 创建后 10-30s 生成窗口，即使 pollAudioReady=false）。
 * - audio_ready → 停止（data 比触发信号新，就绪即停）。
 * - pollAudioReady（再生成中）→ 每 3s——**必须先于 audio_error 早退**（2026-10-07
 *   B3 复审 F2③）：重试/帧卡 action 触发再生成时，data 仍挂上一轮 audio_error
 *   （后端 run 结束才覆写 payload），修前 error 早退把轮询判停、生成完成后 UI 无感知。
 * - audio_error（非再生成态）→ 停止。
 * - 否则（数据已加载、非 audio 轮询）→ 停止。
 */
export function storyboardRefetchInterval(
  query: { state: { data: StoryboardData | undefined; error: Error | null } },
  pollAudioReady: boolean,
  taskFailed: boolean,
  hasPendingPreview = false,
): number | false {
  if (taskFailed) return false; // B2
  const d = query.state.data;
  if (d?.audio_ready) return hasPendingPreview ? 3000 : false;
  if (pollAudioReady) return 3000; // F2③：先于 error 早退（见上）
  if (d?.audio_error) return hasPendingPreview ? 3000 : false;
  // E1: 草稿生成期后端返 200 {status:generating}（非 404）→ 继续每 2s 轮询至落盘。
  // 读的是 query.state.data（select 归一化前的原始响应），占位信号仍在。
  if (isStoryboardGenerating(d)) return 2000;
  // E1: 409=渲染期（storyboard 中变更不可读）/其它错误 → 退避 10s，不 2s 刷屏
  if (query.state.error) return 10000;
  if (pollAudioReady) return 3000;
  // 预览异步化（2026-07-21）：有在途预览时 3s 轮询取 visual_url/preview_error
  if (hasPendingPreview) return 3000;
  return false;
}

/**
 * Storyboard query with optional audio-ready polling.
 *
 * When `pollAudioReady` is true and the current data shows `audio_ready`
 * is still false, this query will refetch every 3 seconds until the
 * backend reports audio generation complete. Draft-generating period returns
 * 200 {status:generating} (not 404) — polled every 2s until storyboard.json
 * lands. 409 (rendering) backs off to 10s (no 2s spam).
 *
 * The {status:generating} placeholder is normalized to `undefined` at the
 * component layer via `select` (raw cache keeps it for the 2s branch above),
 * so consumers' `storyboard &&` guards behave as designed.
 */
export function useStoryboardWithAudioPolling(
  taskId: string | undefined,
  pollAudioReady: boolean,
  taskFailed = false, // B2：分镜页传入；detail.tsx 既有调用用默认值不受影响
  hasPendingPreview = false, // 预览异步化：有在途预览时保持 3s 轮询
) {
  return useQuery({
    queryKey: [STORYBOARD_KEY, taskId],
    queryFn: () => getStoryboard(taskId!),
    enabled: !!taskId,
    // 404 是暂态（生成中），不要烧 react-query 重试；交给 refetchInterval 轮询。
    retry: false,
    select: storyboardSelect,
    refetchInterval: (query) =>
      storyboardRefetchInterval(
        query,
        pollAudioReady,
        taskFailed,
        hasPendingPreview,
      ),
  });
}

export function useUpdateStoryboardFrame(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      frameIndex,
      updates,
    }: {
      frameIndex: number;
      updates: {
        narration?: string;
        image_prompt?: string;
        search_term?: string;
        layout_content?: Record<string, unknown> | null;
        visual_source?: string | null;
      };
    }) => updateStoryboardFrame(taskId, frameIndex, updates),
    onMutate: async ({ frameIndex, updates }) => {
      await qc.cancelQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      const prev = qc.getQueryData<StoryboardData>([STORYBOARD_KEY, taskId]);
      if (prev) {
        qc.setQueryData<StoryboardData>([STORYBOARD_KEY, taskId], {
          ...prev,
          frames: prev.frames.map((f) =>
            f.index === frameIndex ? { ...f, ...updates } : f,
          ),
        });
      }
      return { prev };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData([STORYBOARD_KEY, taskId], ctx.prev);
      }
      // M8: 此前静默回滚——用户文本被悄悄还原无任何提示。
      toast.error(
        `保存失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
    },
  });
}

export function useRenderFromDraft(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params?: {
      frameEdits?: Record<string, Record<string, string>>;
      quality?: "draft" | "standard" | "high" | "preview";
    }) => renderFromDraft(taskId, params?.frameEdits, params?.quality),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY] });
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY, taskId] });
    },
    // 乐观跳转后（分镜页 onMutate 即跳详情页）失败仍可见
    onError: (err) => {
      toast.error(
        `渲染启动失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

export function useUpdateStory(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (story: string) => updateStory(taskId, story),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY, taskId] });
    },
    onError: (err) => {
      // 故事确认卡只显示 isPending，失败后按钮恢复常态，编辑内容看起来已保存。
      toast.error(
        `故事保存失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

export function useRegenerateFrame(
  taskId: string,
  opts?: { onMutate?: (i: number) => void; onSyncError?: (i: number) => void },
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      frameIndex: number;
      mode: "all" | "audio_only" | "visual_only";
    }) => regenerateFrame(taskId, args.frameIndex, args.mode),
    onMutate: (args) => opts?.onMutate?.(args.frameIndex),
    onSuccess: () => {
      // 202 已受理——终态经 storyboard 轮询（帧上 regen_state）到达；
      // 这里只做轻量失效触发第一次轮询，不标 dirty（后台成功才标）。
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
    },
    onError: (err, args) => {
      // 同步校验错误（400/404/409/422）——立即释放 pending（不会有 storyboard 变化到来）
      opts?.onSyncError?.(args.frameIndex);
      toast.error(
        `重新生成失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

export function useUpdateStoryboardConfig(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: {
      // F4（2026-09-28）：三分态——未动=键不发；「默认/跟随项目」=显式 null；选定=值
      voice_id?: string | null;
      tts_speed?: number;
      bgm_volume?: number;
      subtitle_style?: string;
    }) => updateStoryboardConfig(taskId, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY, taskId] });
    },
    onError: (err) => {
      // 显式 toast——写 mutation 缺 onError 会吞错（记忆 csrf_cookie_only_on_auth_post）
      toast.error(
        `音频设置保存失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

// B4（2026-09-28 edits 修复批）：批量重生全部配音
export function useRegenerateAllAudio(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => regenerateAllAudio(taskId),
    onSuccess: () => {
      // 帧级 regen_state running 起步——立即失效让调用方轮询看到
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY, taskId] });
    },
    onError: (err) => {
      toast.error(
        `批量重生配音失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

// ── Subtitle editing（2026-07-27 T15） ─────────────────────────────────────

const SUBTITLE_KEY = "videoSubtitle";

export function useSubtitle(taskId: string, enabled: boolean) {
  return useQuery({
    queryKey: [SUBTITLE_KEY, taskId],
    queryFn: () => getSubtitle(taskId),
    enabled,
  });
}

export function useUpdateSubtitle(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cues: Array<{ start: string; end: string; text: string }>) =>
      updateSubtitle(taskId, cues),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: [SUBTITLE_KEY, taskId] });
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      // 2026-07-29：该任务原设「无字幕」，手改字幕已自动恢复字幕显示
      // （修前 subtitle_style="none" 让 worker 永不烧录，改了也白改）
      if (data?.subtitle_style_restored) {
        toast.success(
          "字幕已保存，并已自动恢复字幕显示（该任务原设为「无字幕」），点「重新合成」烧入新字幕",
        );
      } else {
        toast.success("字幕已保存，点「重新合成」烧入新字幕");
      }
    },
    onError: (err) => {
      toast.error(
        `字幕保存失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

export function usePreviewFrame(
  taskId: string,
  opts?: { onMutate?: (i: number) => void },
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (frameIndex: number) => previewFrame(taskId, frameIndex),
    onMutate: (i) => opts?.onMutate?.(i),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
    },
    // 异步化后此处只剩 400/404/409 校验错误（生成失败改走帧上 preview_error 字段）
    onError: (err) => {
      toast.error(
        `预览图生成失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

/** M1-2: bulk-generate previews for all frames lacking visual_url. */
export function usePreviewAll(
  taskId: string,
  opts?: { onStarted?: () => void },
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => previewAll(taskId),
    onSuccess: () => {
      opts?.onStarted?.();
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
    },
    onError: (err) => {
      toast.error(
        `批量预览启动失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

/** P3-3/BUG-006：增删帧失败 toast 文案——后端 detail（VideoApiError）自足直显；
 * 仅网络层 fallback（无 message）用通用中文。防「模块前缀+后端主因句」式拼接
 * 这类「模块前缀+后端主因句」语义重复的中英混杂形态（BUG-006）。 */
function _frameEditErrorMessage(err: unknown): string {
  const msg = err instanceof Error ? err.message : "";
  return msg || "操作失败，请重试";
}

/** M-UX: add a storyboard frame (append or insert). */
export function useAddFrame(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      narration?: string;
      image_prompt?: string;
      index?: number;
    }) => addFrame(taskId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
    },
    onError: (err) => {
      // P3-3/BUG-006（2026-09-14）：后端 detail 文案自足（含出路指引），不再拼
      // 模块前缀（修前中英混杂 + 与后端主因句语义重复）；仅 fallback 用通用文案。
      toast.error(_frameEditErrorMessage(err));
    },
  });
}

/** D3（2026-08-19）：用 LLM 重新生成单帧画面描述（regenerate_prompt）。
 * onInvalidate 参数便于测试注入，默认失效 storyboard 缓存。 */
export function useRegenerateFramePrompt(
  taskId: string | undefined,
  onInvalidate?: () => void,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (frameIndex: number) =>
      regenerateFramePrompt(taskId!, frameIndex),
    onSuccess: () => {
      (
        onInvalidate ??
        (() =>
          void qc.invalidateQueries({
            queryKey: [STORYBOARD_KEY, taskId],
          }))
      )();
    },
    onError: (err) => {
      toast.error(
        `生成画面描述失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

/** M-UX: delete a storyboard frame. */
export function useDeleteFrame(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (index: number) => deleteFrame(taskId, index),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
    },
    onError: (err) => {
      // P3-3/BUG-006：同 useAddFrame——detail 自足，前缀去重。
      toast.error(_frameEditErrorMessage(err));
    },
  });
}

export function useReorderFrames(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      fromIndex,
      toIndex,
    }: {
      fromIndex: number;
      toIndex: number;
    }) => reorderFrames(taskId, fromIndex, toIndex),
    onMutate: async ({ fromIndex, toIndex }) => {
      await qc.cancelQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      const prevSb = qc.getQueryData<StoryboardData>([STORYBOARD_KEY, taskId]);
      if (prevSb) {
        const frames = [...prevSb.frames];
        const [moved] = frames.splice(fromIndex, 1);
        if (!moved) return {};
        const insertPos = toIndex < fromIndex ? toIndex : toIndex - 1;
        frames.splice(Math.max(insertPos, 0), 0, moved);
        // Re-index immutably: build new frame objects so the cached prevSb.frames
        // objects are not mutated in place (React Query structural sharing + the
        // onError rollback both rely on prevSb staying pristine).
        const reindexed = frames.map((f, i) => ({ ...f, index: i }));
        qc.setQueryData<StoryboardData>([STORYBOARD_KEY, taskId], {
          ...prevSb,
          frames: reindexed,
        });
      }
      return { prevSb };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.prevSb) {
        qc.setQueryData([STORYBOARD_KEY, taskId], ctx.prevSb);
      }
      // M8: 此前拖拽顺序静默弹回。
      toast.error(
        `重排失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
    },
  });
}

// ── Providers（生成模型，Task 6） ─────────────────────────────────────────

const PROVIDERS_KEY = "videoProviders";

/**
 * 可选生成模型。可用性只取决于后端 key 配置，不会变动，故缓存较久
 * （staleTime 长一点，避免每次开对话框都打一次）。
 */
export function useProviders() {
  return useQuery({
    queryKey: [PROVIDERS_KEY],
    queryFn: listProviders,
    staleTime: 5 * 60_000,
  });
}

// ── BGM ──────────────────────────────────────────────────────────────────

const BGM_KEY = "videoBGM";

export function useBGMList() {
  return useQuery({
    queryKey: [BGM_KEY],
    queryFn: listBGM,
  });
}

export function useBGMUpload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadBGM(file),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [BGM_KEY] });
    },
    onError: (err) => {
      // 修前失败只体现在按钮变红 + title 悬浮提示（鼠标不悬停完全看不到）。
      toast.error(
        `背景音乐上传失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

// ── Script extraction (fixed mode doc upload) ─────────────────────────────

export function useExtractScript() {
  return useMutation({
    // T6（2026-09-10 独立管线计划）：withImages=true 时 docx 走原位图片提取
    // （返回 extract_id/expires_at/ignored_images + 【图片N】 标记文本）。
    mutationFn: ({ file, withImages }: { file: File; withImages?: boolean }) =>
      extractScript(file, withImages ?? false),
    onError: (err) => {
      toast.error(
        `文档解析失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

// ── Frame visual upload ──────────────────────────────────────────────────

export function useUploadFrameVisual(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ frameIndex, file }: { frameIndex: number; file: File }) =>
      uploadFrameVisual(taskId, frameIndex, file),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY, taskId] });
    },
    // M8: 此前完全静默——failed 态 409、50MB/类型 400 用户毫无感知。
    onError: (err) => {
      toast.error(
        `上传替换失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

// ── Recompose (batch re-render) ───────────────────────────────────────

/** P2-4：recompose/re-render 失败引导的导航回调（hooks 层不直接引 router）。 */
export interface RecomposeNavOptions {
  /** 409 引导 action「去编辑分镜」的导航回调（通常 router.push 分镜页）。 */
  onEditStoryboard?: () => void;
}

function toastAction409(
  err: unknown,
  prefix: string,
  actionLabel: string,
  opts?: RecomposeNavOptions,
) {
  // P2-4/BUG-007（2026-09-14）：唯一 toast 出口收敛到这里——409 时带具体原因 +
  // 出路指引 action（8s 可读完），其他错误维持通用 toast。组件层（detail 页
  // handleRecomposeError）只保留帧高亮，不发 toast（防双 toast）。
  if (err instanceof VideoApiError && err.status === 409) {
    toast.error(`${prefix}：${err.message}`, {
      duration: 8000,
      action: opts?.onEditStoryboard
        ? createElement(
            "button",
            {
              onClick: opts.onEditStoryboard,
              className:
                "rounded bg-[var(--primary)] px-2 py-1 text-xs text-white",
            },
            actionLabel,
          )
        : undefined,
    });
    return;
  }
  toast.error(`${prefix}：${err instanceof Error ? err.message : "未知错误"}`);
}

export function useRecomposeTask(taskId: string, opts?: RecomposeNavOptions) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => recomposeTask(taskId),
    // F4: don't invalidate video src here — new mp4 doesn't exist yet (async).
    // useVideoTask will poll composing → ready; the useEffect in detail page
    // handles the src refresh on status transition.
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY, taskId] });
      // F7（2026-09-28 edits 修复批）：补失效——storyboard（字幕产物/pending 徽标随
      // recompose 刷新）与裸列表键（TanStack 前缀匹配下 [VIDEO_KEY, taskId] 不覆盖
      // 列表 [VIDEO_KEY]，列表徽标/时长陈旧）。
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY] });
    },
    onError: (err) => {
      toastAction409(err, "重新渲染失败", "去编辑分镜", opts);
    },
  });
}

// Task 20c: 项目已更新 → 重新生成视觉（force 刷新项目角色/风格）
export function useReRenderTask(taskId: string, opts?: RecomposeNavOptions) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => reRenderTask(taskId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY, taskId] });
      // M3: re-render 经 on_frame_done 把新 visual_url/local_visual_path 写回 storyboard——
      // 不失效的话 FrameStrip/面板在任务回 ready 后仍显示旧分镜图。
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      // F7 增补（2026-09-28）：同 useRecomposeTask——裸列表键 [VIDEO_KEY] 缺失效。
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY] });
    },
    onError: (err) => {
      toastAction409(err, "重新生成失败", "去检查帧素材", opts);
    },
  });
}

// ── Course Projects ────────────────────────────────────────────────────────

const PROJECT_KEY = "courseProjects";

export function useCourseProjects() {
  return useQuery({
    queryKey: [PROJECT_KEY],
    queryFn: listCourseProjects,
    staleTime: 1000 * 60,
  });
}

// Task 20b: 单个项目（取 character_sheets[0].front_url 作帧预览旁的角色参考缩略图）
export function useCourseProject(projectId: string | null | undefined) {
  return useQuery({
    queryKey: [PROJECT_KEY, projectId],
    queryFn: () => getCourseProject(projectId!),
    enabled: !!projectId,
    staleTime: 1000 * 60,
  });
}

export function useCreateCourseProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CourseProjectInput) => createCourseProject(input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "项目操作失败，请重试");
    },
  });
}

export function useUpdateCourseProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      input,
    }: {
      id: string;
      input: Partial<CourseProjectInput>;
    }) => updateCourseProject(id, input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "项目操作失败，请重试");
    },
  });
}

export function useDeleteCourseProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteCourseProject(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "项目操作失败，请重试");
    },
  });
}

// ── C6（决策 4）：风格锚图管理 mutations ────────────────────────────────────

/** AI 生成风格样图（不写项目字段；预览满意后 set 固化）。 */
export function useGenerateStyleAnchor(projectId: string) {
  return useMutation({
    mutationFn: (vars: { prompt: string; includePeople: boolean }) =>
      generateStyleAnchorImage(projectId, vars.prompt, vars.includePeople),
    onError: (err) => {
      toast.error(
        err instanceof Error ? err.message : "风格图生成失败，请重试",
      );
    },
  });
}

/** 固化候选 / 上传图片 → 写项目锚图（后端替换时删除旧文件）。
 * T5：body 可为恢复态文件名 `{candidate}`（挂载恢复后的固化路径）。 */
export function useSetStyleAnchor(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (
      body: { candidate_path: string } | { candidate: string } | { file: File },
    ) => setStyleAnchorImage(projectId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "设置风格锚图失败");
    },
  });
}

/** T5（2026-08-31 候选持久化）：弃用未固化候选（「换一张」）——后端清字段+删文件。 */
export function useClearStyleAnchorCandidate(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => clearStyleAnchorCandidate(projectId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: () => {
      // 弃用失败不阻塞 UI（本地候选已清）；后端字段残留最多导致下次挂载多恢复
      // 一次可再弃的候选，不构成数据损坏——降级为静默。
    },
  });
}

/** 删除锚图（清字段 + 删文件）。 */
export function useDeleteStyleAnchor(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteStyleAnchorImage(projectId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "删除风格锚图失败");
    },
  });
}

// ── M6（决策 12/15/16/18）：道具资产 mutations（仅手动上传，无 AI 生成入口）──

/** 上传道具（multipart：name/description/main_image/extra_images[]）。 */
export function useUploadPropAsset(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (fd: FormData) => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/video/projects/${encodeURIComponent(projectId)}/props/upload`,
        { method: "POST", body: fd, credentials: "include" },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as {
          detail?: unknown;
        } | null;
        const detail = formatFastApiErrorDetail(body?.detail);
        throw new Error(detail ? `上传道具失败：${detail}` : "上传道具失败");
      }
      return res.json();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "上传道具失败");
    },
  });
}

/** 删除道具（字段 + 磁盘目录同步清理）。 */
export function useDeletePropAsset(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (propId: string) => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/video/projects/${encodeURIComponent(projectId)}/props/${encodeURIComponent(propId)}`,
        { method: "DELETE", credentials: "include" },
      );
      if (!res.ok) throw new Error("删除道具失败");
      return res.json();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: () => {
      toast.error("删除道具失败");
    },
  });
}

// Task 10/11: 角色三视图生成/上传
export function useGenerateCharacterSheet(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      name: string;
      description: string;
      features: string;
      replace_id?: string; // M1: 重新生成时传原 sheet 稳定 id → 后端原子替换（下标寻址退役）
    }) => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/video/projects/${encodeURIComponent(projectId)}/character-sheets/generate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          credentials: "include",
        },
      );
      if (!res.ok) {
        // FP1-8: 透出服务端 detail（配额/审查/超时），不再泛化
        const body = (await res.json().catch(() => null)) as {
          detail?: unknown;
        } | null;
        const detail = formatFastApiErrorDetail(body?.detail);
        throw new Error(
          detail ? `生成角色三视图失败：${detail}` : "生成角色三视图失败",
        );
      }
      return res.json();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "生成角色三视图失败");
    },
  });
}

export function useUploadCharacterSheet(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (fd: FormData) => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/video/projects/${encodeURIComponent(projectId)}/character-sheets/upload`,
        { method: "POST", body: fd, credentials: "include" },
      );
      if (!res.ok) {
        // FP1-8: 透出服务端 detail（配额/审查/超时），不再泛化
        const body = (await res.json().catch(() => null)) as {
          detail?: unknown;
        } | null;
        const detail = formatFastApiErrorDetail(body?.detail);
        throw new Error(
          detail ? `上传角色三视图失败：${detail}` : "上传角色三视图失败",
        );
      }
      return res.json();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "上传角色三视图失败");
    },
  });
}

export function useDeleteCharacterSheet(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (sheetId: string) => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/video/projects/${encodeURIComponent(projectId)}/character-sheets/${encodeURIComponent(sheetId)}`,
        { method: "DELETE", credentials: "include" },
      );
      if (!res.ok) throw new Error("删除角色参考图失败");
      return res.json();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: () => {
      toast.error("删除角色参考图失败");
    },
  });
}

export function useReorderCharacterSheets(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    // M1: body 传完整 id 数组（新顺序即数组顺序；首个 = 主角色）
    mutationFn: async (body: { ids: string[] }) => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/video/projects/${encodeURIComponent(projectId)}/character-sheets/reorder`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          credentials: "include",
        },
      );
      if (!res.ok) throw new Error("排序角色参考图失败");
      return res.json();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PROJECT_KEY] });
    },
    onError: () => {
      toast.error("排序角色参考图失败");
    },
  });
}

// Task 16/17: ViMax 生成后逐镜重做
export function useRedoVimaxShot(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (shotId: number) => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/video/tasks/${encodeURIComponent(taskId)}/vimax-shots/${shotId}/redo`,
        { method: "POST", credentials: "include" },
      );
      if (!res.ok) throw new Error("重做镜头失败");
      return res.json();
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY] });
    },
    onError: (err) => {
      // 重做镜头失败时按钮只是恢复可点，用户无法区分「没点上」与「服务器拒绝」。
      toast.error(
        `重做镜头失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

// ── Draft Audio ────────────────────────────────────────────────────────────

export function useGenerateAudio(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => generateDraftAudio(taskId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      void qc.invalidateQueries({ queryKey: [VIDEO_KEY, taskId] });
    },
    onError: (err) => {
      // audio-preview-bar 会就地显示错误文案，但该条只在音频区可见；
      // 用户可能已滚到别处，故同时给一次全局提示（2026-07-29）。
      toast.error(
        `音频生成失败：${err instanceof Error ? err.message : "未知错误"}`,
      );
    },
  });
}

// ── Frame Visual Source ──────────────────────────────────────────────────

export function useUpdateFrameVisualSource(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      frameIndex,
      visualSource,
    }: {
      frameIndex: number;
      visualSource: string;
    }) => updateFrameVisualSource(taskId, frameIndex, visualSource),
    onMutate: async ({ frameIndex, visualSource }) => {
      await qc.cancelQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      const prev = qc.getQueryData<StoryboardData>([STORYBOARD_KEY, taskId]);
      if (prev) {
        qc.setQueryData<StoryboardData>([STORYBOARD_KEY, taskId], {
          ...prev,
          frames: prev.frames.map((f) =>
            f.index === frameIndex ? { ...f, visual_source: visualSource } : f,
          ),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData([STORYBOARD_KEY, taskId], ctx.prev);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
    },
  });
}

export function useUpdateFrameLayout(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      frameIndex,
      layout,
    }: {
      frameIndex: number;
      layout: string | null;
    }) => updateFrameLayout(taskId, frameIndex, layout),
    onMutate: async ({ frameIndex, layout }) => {
      await qc.cancelQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      const prev = qc.getQueryData<StoryboardData>([STORYBOARD_KEY, taskId]);
      if (prev) {
        qc.setQueryData<StoryboardData>([STORYBOARD_KEY, taskId], {
          ...prev,
          frames: prev.frames.map((f) =>
            f.index === frameIndex ? { ...f, layout } : f,
          ),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData([STORYBOARD_KEY, taskId], ctx.prev);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
    },
  });
}

export function useBulkDisableGraphic(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => bulkDisableGraphic(taskId),
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      const prev = qc.getQueryData<StoryboardData>([STORYBOARD_KEY, taskId]);
      if (prev) {
        // 乐观翻转：graphic → null（=主引擎）；graphic_spec 保留由后端负责
        qc.setQueryData<StoryboardData>([STORYBOARD_KEY, taskId], {
          ...prev,
          frames: prev.frames.map((f) =>
            f.visual_source === "graphic" ? { ...f, visual_source: null } : f,
          ),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData([STORYBOARD_KEY, taskId], ctx.prev);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
    },
  });
}

// ── SSE Progressive Preview ──────────────────────────────────────────────────

/**
 * Subscribe to SSE frame preview events for a running task.
 *
 * Returns a map of frame_index → PreviewFrame that updates in real-time
 * as the backend generates visuals. Automatically closes when the task
 * completes or the component unmounts.
 */
export function useTaskEvents(taskId: string | undefined) {
  const [previewFrames, setPreviewFrames] = useState<Map<number, PreviewFrame>>(
    new Map(),
  );
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!taskId) return;
    setPreviewFrames(new Map());
    setDone(false);

    let es: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    let finished = false;

    const connect = () => {
      if (finished) return;
      es = new EventSource(
        `${getBackendBaseURL()}/api/video/tasks/${encodeURIComponent(taskId)}/events`,
      );

      es.addEventListener("frame", (e) => {
        try {
          const data = JSON.parse(e.data) as PreviewFrame;
          setPreviewFrames((prev) => {
            const next = new Map(prev);
            next.set(data.frame_index, data);
            return next;
          });
        } catch {
          // Ignore malformed events
        }
      });

      es.addEventListener("done", () => {
        finished = true;
        setDone(true);
        es?.close();
      });

      es.onerror = () => {
        // 手动退避重连（替代原生 3s 无退避重连 —— 故障期连接风暴）
        es?.close();
        es = null;
        if (finished) return;
        if (attempts >= MAX_SSE_RETRIES) return; // 放弃：状态轮询仍兜底最终态
        retryTimer = setTimeout(connect, nextBackoffMs(attempts));
        attempts += 1;
      };
    };

    connect();

    return () => {
      finished = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
    };
  }, [taskId]);

  return { previewFrames, done };
}
