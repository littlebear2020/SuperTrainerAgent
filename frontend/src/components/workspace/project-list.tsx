"use client";

import { useRouter } from "next/navigation";

import { resolveAgentName } from "@/core/agents";
import { agentBadge } from "@/core/agents/appearance";
import { useGlassStyle } from "@/core/theme/glass";
import type { AgentThread } from "@/core/threads/types";
import { formatDateTime } from "@/core/utils/datetime";
import { cn } from "@/lib/utils";

// 徽标配色单一事实源 = core/agents/appearance.ts（badge 经 agentBadge 派生，
// Q5 拍板 ppt-master 统一 teal——修前本表为 amber 分叉）。label/emoji/accent
// 为本列表面自有呈现。
const TYPE_MAP: Record<
  string,
  { label: string; icon: string; color: string; accent: string }
> = {
  "course-video": {
    label: "视频课程",
    icon: "🎬",
    color: "text-indigo-500",
    accent: "rgba(99, 102, 241, 0.12)",
  },
  // 2026-09-11 ppt-master 6.3.0 批次：course-ppt 完全退役，别名归一到 ppt-master。
  "ppt-master": {
    label: "PPT",
    icon: "📊",
    color: "text-teal-500",
    accent: "rgba(20, 184, 166, 0.1)",
  },
  // 2026-09-09 创意工作合并：旧 5 壳（card-poster/case-article/design-specialist/
  // knowledge-extraction/video-clip）经 resolveAgentName 归一后命中本项。
  "creative-studio": {
    label: "创意工作",
    icon: "🎨",
    color: "text-fuchsia-500",
    accent: "rgba(232, 121, 249, 0.1)",
  },
};

interface ProjectListProps {
  threads: AgentThread[];
}

export function ProjectList({ threads }: ProjectListProps) {
  const router = useRouter();
  const { glassStyle, isDark } = useGlassStyle();

  if (threads.length === 0) {
    return (
      <div
        className={cn(
          "flex rounded-2xl border border-dashed p-10 text-center",
          isDark ? "border-white/10" : "border-indigo-200/50",
        )}
      >
        <div className="mx-auto">
          <p
            className={cn(
              "mb-1 text-sm",
              isDark ? "text-slate-400" : "text-slate-500",
            )}
          >
            还没有项目
          </p>
          <p
            className={cn(
              "text-xs",
              isDark ? "text-slate-500" : "text-slate-400",
            )}
          >
            选择上方工作流开始你的第一个项目
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      style={glassStyle()}
      className="interactive-glass overflow-hidden rounded-2xl p-4"
    >
      <div className="flex flex-col gap-2">
        {threads.slice(0, 10).map((thread, i) => {
          const meta = thread.metadata as Record<string, unknown> | undefined;
          const projectType = (meta?.project_type as string) ?? undefined;
          const typeInfo = projectType
            ? TYPE_MAP[resolveAgentName(projectType)]
            : undefined;
          const title = thread.values?.title || "未命名项目";
          const updatedAt = thread.updated_at
            ? formatDateTime(thread.updated_at)
            : "";

          return (
            <button
              key={thread.thread_id}
              className={cn(
                "group relative z-[1] flex items-center gap-3.5 rounded-xl p-3 text-left transition-all duration-200",
                isDark ? "hover:bg-white/5" : "hover:bg-indigo-500/5",
              )}
              onClick={() =>
                router.push(`/workspace/chats/${thread.thread_id}`)
              }
            >
              {/* Type icon */}
              <div
                className={cn(
                  "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-base",
                )}
                style={typeInfo ? { background: typeInfo.accent } : undefined}
              >
                {typeInfo?.icon ?? "💬"}
              </div>

              {/* Info */}
              <div className="min-w-0 flex-1">
                <div
                  className={cn(
                    "truncate text-[13.5px] font-semibold",
                    isDark ? "text-white" : "text-slate-800",
                  )}
                >
                  {title}
                </div>
                <div className="mt-1 flex items-center gap-2.5 text-xs">
                  {typeInfo && (
                    <span
                      className={`rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase ${agentBadge(projectType)}`}
                    >
                      {typeInfo.label}
                    </span>
                  )}
                  {projectType === undefined && (
                    <span
                      className={cn(
                        "rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase",
                        isDark
                          ? "bg-white/5 text-slate-400"
                          : "bg-indigo-500/5 text-slate-500",
                      )}
                    >
                      自由对话
                    </span>
                  )}
                  <span
                    className={cn(isDark ? "text-slate-500" : "text-slate-400")}
                  >
                    🕐 {updatedAt}
                  </span>
                </div>
              </div>

              {/* Arrow */}
              <span
                className={cn(
                  "text-[11px]",
                  isDark ? "text-slate-500" : "text-slate-400",
                )}
              >
                ›
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
