import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useRecomposeTask: () => ({ mutate: vi.fn(), isPending: false }),
  getStallWarning: () => null,
}));

import { TaskCard } from "@/components/workspace/video-gen/video-gen-page";
import type { VideoTaskBrief } from "@/core/video-gen/types";

const baseTask: VideoTaskBrief = {
  id: "vg_t1",
  user_id: "u1",
  domain: "video_gen",
  task_type: "ai_image",
  status: "failed",
  progress: 85,
  title: "测试任务",
  error_message: "ffmpeg timeout",
  retry_count: 0,
  dirty_frames: [],
  created_at: "2026-07-22T09:00:00Z",
  updated_at: "2026-07-22T10:00:00Z",
  thumbnail_url: null,
  cost: null,
  currency: null,
};

const noop = () => undefined;

test("failed 任务卡片显示「重试合成」（修前只有删除）", () => {
  const html = renderToStaticMarkup(
    createElement(TaskCard, {
      task: baseTask,
      onDelete: noop,
      previewTaskId: null,
      setPreviewTaskId: noop,
      onNavigate: noop,
    }),
  );
  expect(html).toContain("重试合成");
});

test("ready 任务卡片不显示「重试合成」（旧行为:播放按钮）", () => {
  const html = renderToStaticMarkup(
    createElement(TaskCard, {
      task: { ...baseTask, status: "ready" },
      onDelete: noop,
      previewTaskId: null,
      setPreviewTaskId: noop,
      onNavigate: noop,
    }),
  );
  expect(html).not.toContain("重试合成");
});

// ── 批次 C Phase D (2026-08-13)：TaskCard 花费渲染 ───────────────────────

test("TaskCard 副行显示花费金额（cost 有值）", () => {
  const html = renderToStaticMarkup(
    createElement(TaskCard, {
      task: { ...baseTask, cost: 12.5, currency: "CNY" },
      onDelete: noop,
      previewTaskId: null,
      setPreviewTaskId: noop,
      onNavigate: noop,
    }),
  );
  expect(html).toContain("花费");
  expect(html).toContain("¥12.50");
});

test("TaskCard 副行 cost 为 null 显示「—」（区别于免费）", () => {
  const html = renderToStaticMarkup(
    createElement(TaskCard, {
      task: baseTask,
      onDelete: noop,
      previewTaskId: null,
      setPreviewTaskId: noop,
      onNavigate: noop,
    }),
  );
  expect(html).toContain("花费 —");
});

// ── D5 (2026-08-14)：进行中任务不显示花费段 ─────────────────────────────

test("进行中任务卡片不显示花费段（避免恒显示「花费 —」）", () => {
  const html = renderToStaticMarkup(
    createElement(TaskCard, {
      task: { ...baseTask, status: "running", cost: 12.5, currency: "CNY" },
      onDelete: noop,
      previewTaskId: null,
      setPreviewTaskId: noop,
      onNavigate: noop,
    }),
  );
  expect(html).not.toContain("花费");
});

test("cancelled 任务卡片显示花费段（终态对齐后端 TERMINAL_STATUSES）", () => {
  // 被取消的任务可能已产生真实 LLM/媒体花费，取消后仍须可见（审查 Finding 2）
  const html = renderToStaticMarkup(
    createElement(TaskCard, {
      task: { ...baseTask, status: "cancelled", cost: 12.5, currency: "CNY" },
      onDelete: noop,
      previewTaskId: null,
      setPreviewTaskId: noop,
      onNavigate: noop,
    }),
  );
  expect(html).toContain("花费");
  expect(html).toContain("¥12.50");
});
