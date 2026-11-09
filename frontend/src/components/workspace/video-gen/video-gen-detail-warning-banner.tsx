"use client";

/**
 * 任务警告横幅（2026-10-06 拆分批自 video-gen-detail 的 127 行 IIFE 抽出）。
 * 字幕/时长/CJK/覆盖/漂移/音色/占位/i2i/降级九路 sidecar 警告 + 自动重试中性提示；
 * 谓词口径（ready/failed 才出卡、活跃态 retry>0 出中性提示、cancelled 隐藏）
 * 原样保留，历史事故注释见函数体。
 */
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { VideoTaskDetail } from "@/core/video-gen/types";

export function DetailWarningBanner({
  task,
  recomposePending,
  onRecompose,
}: {
  task: VideoTaskDetail;
  recomposePending: boolean;
  onRecompose: () => void;
}) {
  const r = task.result ?? {};
  const sub = r.subtitle_status as
    | { status?: string; error?: string }
    | undefined;
  const durWarn = r.duration_warning as string | undefined;
  const cjkWarn = r.remotion_cjk_warning as string | undefined;
  const covWarn = r.ai_video_coverage_warning as string | undefined;
  const driftWarn = r.av_drift_warning as string | undefined;
  const voiceWarn = r.voice_mode_warning as string | undefined;
  // 2026-07-29：worker 侧素材获取失败降级为黑场占位（修前全帧黑场也 status=ready，
  // 与完美渲染不可区分，用户白等一次完整渲染才发现片子是黑的）
  const placeholderWarn = r.placeholder_warning as string | undefined;
  // 2026-07-29：provider 不支持参考图（i2i）时角色一致性静默失效——
  // 后端一直在写 sidecar，但此前全仓零读取、前端零渲染
  const i2iWarn = r.ai_image_i2i_warning as string | undefined;
  // 2026-08-03：degradations.json 通道（bgm/字幕/转场/音色降级 + 本轮新增的
  // visual_shortfall / preview_i2v_unsupported / prompt_neutralized）。
  // 后端 _attach_degradation_warning 一直在算这个字段，但**前端从未读过**
  // ——整条降级通道对用户不可见（与上面 i2iWarn 是同型事故：后端写 sidecar、
  // 全仓零读取）。
  const degWarn = r.degradation_warning as string | undefined;
  const hasAny =
    sub?.status === "failed" ||
    !!durWarn ||
    !!cjkWarn ||
    !!covWarn ||
    !!driftWarn ||
    !!voiceWarn ||
    !!placeholderWarn ||
    !!i2iWarn ||
    !!degWarn;
  if (!hasAny) return null;
  // 2026-08-19（反馈2-2 P0）：警告卡与「重新合成」只在 ready/failed 显示——
  // 谓词刻意对齐 recompose 端点（routers/video_gen.py:1478 status ∈ {ready,
  // failed}）。修前无 status 判断 + 按钮 disabled 只看 isPending → 自动重试
  // running 态读取上一轮残留警告可点按钮 → 409（vg_91899fe68c3b 事故）。
  // 活跃态（pending/running/composing）+ retry_count>0 → 中性提示（无按钮）；
  // cancelled/draft 一律隐藏——cancelled 任务永远不会后台重试，显示"自动
  // 重试中"是误导（审查 P1：修前草案的"非终态即提示"会命中
  // cancelled+retry_count>0 这一真实可达组合）。
  // N/M 完整信息在 error_message 横幅（下方已渲染「重试 1/2：…」）。
  if (task.status !== "ready" && task.status !== "failed") {
    if (
      ["pending", "running", "composing"].includes(task.status) &&
      (task.retry_count ?? 0) > 0
    ) {
      return (
        <div
          data-testid="auto-retry-notice"
          className="rounded-lg border border-amber-300/50 bg-amber-50 p-3 dark:bg-amber-950/30"
        >
          <p className="text-xs text-amber-700 dark:text-amber-300">
            · 检测到画面生成问题，后台正在自动重试（第 {task.retry_count}{" "}
            次），结束后可在此手动重试
          </p>
        </div>
      );
    }
    return null;
  }
  return (
    <div className="space-y-2 rounded-lg border border-amber-300/50 bg-amber-50 p-3 dark:bg-amber-950/30">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-amber-700 dark:text-amber-300">
          成片存在以下问题（可修复）
        </p>
        {/* Task 20: 行动按钮——重新生成（recompose 重跑 worker，含字幕烧录+视觉重生） */}
        <Button
          size="sm"
          variant="outline"
          className="h-6 border-amber-400 text-xs text-amber-700 hover:bg-amber-100 dark:text-amber-300"
          disabled={recomposePending}
          onClick={onRecompose}
        >
          <RefreshCw className="mr-1 h-3 w-3" />
          {recomposePending ? "重新合成中..." : "重新合成"}
        </Button>
      </div>
      {sub?.status === "failed" && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          · 字幕烧录失败：{sub.error ?? "未知原因"}（视频可能无字幕）
        </p>
      )}
      {durWarn && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          · {durWarn}
        </p>
      )}
      {cjkWarn && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          · {cjkWarn}
        </p>
      )}
      {covWarn && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          · {covWarn}
        </p>
      )}
      {driftWarn && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          · {driftWarn}
        </p>
      )}
      {voiceWarn && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          · {voiceWarn}
        </p>
      )}
      {placeholderWarn && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          · {placeholderWarn}
        </p>
      )}
      {i2iWarn && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          · {i2iWarn}
        </p>
      )}
      {degWarn && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          · {degWarn}
        </p>
      )}
    </div>
  );
}
