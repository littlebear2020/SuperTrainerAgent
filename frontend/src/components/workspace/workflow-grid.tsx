"use client";

import { motion } from "motion/react";

import { agentAppearance } from "@/core/agents/appearance";
import { useGlassStyle } from "@/core/theme/glass";
import { cn } from "@/lib/utils";

// 2026-09-22 工作台恢复：现役 6 agent（09-09 五壳合并进 creative-studio、
// 09-11 course-ppt 退役后）。文案提炼自 backend/seed/agents/*/config.yaml 的
// description（卡片有 line-clamp-2 两行截断，不全文粘贴）；名称对齐产品内
// 现有显示名（project-list TYPE_MAP / demo 页，creative-studio 统一叫「创意工作」）。
export const WORKFLOWS = [
  {
    type: "course-video",
    name: "视频课程专家",
    desc: "从选题到成片：脚本撰写、AI 配音、画面生成、渲染输出",
    tags: ["脚本撰写", "AI配音", "视频生成"],
  },
  {
    type: "vimax-video",
    name: "VIMAX 视频大师",
    desc: "故事/剧情视频：有角色有剧情，角色跨镜一致，逐镜 AI 生成画面",
    tags: ["故事视频", "角色一致", "ViMax 引擎"],
  },
  {
    type: "ppt-master",
    name: "PPT Master",
    desc: "SVG→可编辑 PPTX，专业模板，逐页 AI 生成",
    tags: ["PPT生成", "SVG模板", "可编辑PPTX"],
  },
  {
    // 五壳合并宿主（card-poster/case-article 视觉部分/design-specialist/
    // knowledge-extraction/video-clip），配色沿用 design-specialist 的 fuchsia。
    type: "creative-studio",
    name: "创意工作",
    desc: "创意内容生产：文案、设计、知识萃取、视频剪辑一体化",
    tags: ["知识萃取", "海报设计", "视频剪辑"],
  },
  {
    type: "co-writer",
    name: "协同写作专家",
    desc: "AI 协同写作：改写、润色、续写、结构化成文",
    tags: ["案例研写", "润色改写", "结构化"],
  },
  {
    type: "course-assistant",
    name: "课程助理",
    desc: "自由对话兜底：课程设计问答与任务编排",
    tags: ["自由对话", "课程设计", "任务编排"],
  },
] as const;

interface WorkflowGridProps {
  onSelect: (type: string) => void;
}

function WorkflowCard({
  wf,
  index,
  onSelect,
}: {
  wf: (typeof WORKFLOWS)[number];
  index: number;
  onSelect: (type: string) => void;
}) {
  const { glassStyle, isDark } = useGlassStyle();
  // 图标/配色单一事实源：core/agents/appearance.ts
  const appearance = agentAppearance(wf.type);
  const AppearanceIcon = appearance.icon;

  return (
    <motion.div
      style={glassStyle()}
      className={cn(
        "interactive-glass group cursor-pointer rounded-2xl p-5 transition-transform hover:scale-[1.01]",
      )}
      role="button"
      tabIndex={0}
      onClick={() => onSelect(wf.type)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect(wf.type);
      }}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        type: "spring",
        stiffness: 100,
        damping: 20,
        delay: 0.08 * index,
      }}
    >
      {/* Icon */}
      <div
        className={`relative z-[1] mb-4 inline-flex h-11 w-11 items-center justify-center rounded-xl transition-all duration-300 ${appearance.bg} ${appearance.text}`}
      >
        <AppearanceIcon className="h-5 w-5" />
      </div>

      {/* Name */}
      <h3
        className={cn(
          "relative z-[1] mb-1.5 text-sm font-extrabold tracking-wide",
          isDark ? "text-white" : "text-slate-800",
        )}
      >
        {wf.name}
      </h3>

      {/* Description */}
      <p
        className={cn(
          "relative z-[1] mb-4 line-clamp-2 min-h-[2.25rem] text-xs leading-normal font-semibold",
          isDark ? "text-slate-400" : "text-slate-500",
        )}
      >
        {wf.desc}
      </p>

      {/* Tags */}
      <div className="relative z-[1] mt-auto flex flex-wrap gap-1.5">
        {wf.tags.map((tag) => (
          <span
            key={tag}
            className={cn(
              "rounded-md px-2 py-0.5 text-[10px] font-bold",
              isDark
                ? "bg-white/5 text-slate-400"
                : "bg-indigo-500/5 text-slate-500",
            )}
          >
            {tag}
          </span>
        ))}
      </div>
    </motion.div>
  );
}

function WorkflowGrid({ onSelect }: WorkflowGridProps) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {WORKFLOWS.map((wf, i) => (
        <WorkflowCard key={wf.type} wf={wf} index={i} onSelect={onSelect} />
      ))}
    </div>
  );
}
