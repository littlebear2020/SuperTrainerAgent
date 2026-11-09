"use client";

import { motion } from "motion/react";
import { useRouter } from "next/navigation";
import { useCallback, useRef } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import { useThreads } from "@/core/threads/hooks";
import { cn } from "@/lib/utils";

import { ProjectList } from "./project-list";
import { WorkflowGrid } from "./workflow-grid";

// 关键词 → Agent 映射（按优先级顺序匹配）。
// 2026-09-22 工作台恢复：路由表同步收缩到现役 6 agent——course-ppt 关键词由
// ppt-master 承接（09-11 退役）；card-poster/knowledge-extraction/design-specialist/
// video-clip 关键词由 creative-studio 承接（09-09 五壳合并）；case-article 关键词
// 由 co-writer 承接（专职写作入口）。无命中落 course-assistant 兜底。
export const ROUTING_RULES: { agent: string; keywords: string[] }[] = [
  {
    agent: "course-video",
    keywords: [
      "视频课程",
      "培训视频",
      "教学视频",
      "课程视频",
      "制作视频",
      "录制课程",
    ],
  },
  {
    agent: "vimax-video",
    keywords: [
      // 故事/剧情/叙事 videos route to the ViMax backend-engine agent (2026-07-15 split);
      // course-video is now Remotion direct-write (讲解/科普, no story/character).
      "故事视频",
      "剧情视频",
      "剧情短片",
      "叙事视频",
      "微电影",
      "故事片",
      "动画故事",
    ],
  },
  {
    // course-ppt 09-11 退役，关键词由 ppt-master 承接。（matchAgent 双侧
    // toLowerCase，大小写变体无需重复列出。）
    agent: "ppt-master",
    keywords: ["PPT", "课件", "幻灯片", "演示文稿"],
  },
  {
    // 五壳合并承接（含 video-clip 的剪辑类关键词——creative-studio 的
    // config.yaml skills 保留 video-clip，剪辑是其现役能力，不得落兜底）。
    agent: "creative-studio",
    keywords: [
      "知识卡片",
      "海报",
      "宣传图",
      "信息图",
      "卡片设计",
      "知识萃取",
      "经验提炼",
      "知识提取",
      "提炼知识",
      "视觉设计",
      "品牌视觉",
      "视频剪辑",
      "剪辑视频",
      "视频切片",
      "提取精华",
      "剪辑",
    ],
  },
  {
    // case-article 并入 creative-studio 后，写作类关键词统一归专职写作入口 co-writer。
    agent: "co-writer",
    keywords: [
      "案例研究",
      "案例分析",
      "研究报告",
      "写文章",
      "案例文章",
      "公众号",
      "润色",
      "续写",
    ],
  },
];

export function matchAgent(input: string): string | null {
  const lower = input.toLowerCase();
  for (const rule of ROUTING_RULES) {
    if (rule.keywords.some((kw) => lower.includes(kw.toLowerCase()))) {
      return rule.agent;
    }
  }
  return null;
}

export default function WorkspaceHome() {
  const router = useRouter();
  const { data: threads } = useThreads();
  const inputRef = useRef<HTMLInputElement>(null);
  const { glassStyle, isDark } = useGlassStyle();
  // DEF-3（2026-10-07）：UI 文案走 i18n（workspace.home.*）——此前硬编码中文，
  // cookie=en-US 首屏残片。ROUTING_RULES 的关键词是路由匹配语义，非 UI 文案，
  // 刻意不入 i18n（英文态仍按中文关键词命中）。
  const { t } = useI18n();

  const startWorkflow = useCallback(
    (agentName: string) => {
      const userInput = inputRef.current?.value.trim();
      const url = userInput
        ? `/workspace/agents/${agentName}/chats/new?msg=${encodeURIComponent(userInput)}`
        : `/workspace/agents/${agentName}/chats/new`;
      router.push(url);
    },
    [router],
  );

  const startFree = useCallback(() => {
    const userInput = inputRef.current?.value.trim();
    if (!userInput) {
      router.push("/workspace/agents/course-assistant/chats/new");
      return;
    }
    const matchedAgent = matchAgent(userInput);
    const target = matchedAgent ?? "course-assistant";
    router.push(
      `/workspace/agents/${target}/chats/new?msg=${encodeURIComponent(userInput)}`,
    );
  }, [router]);

  return (
    <div className="relative flex h-full flex-col overflow-y-auto">
      {/* Ambient background — 3 orbs */}
      <div className="ws-ambient" aria-hidden="true" />
      <div className="ws-ambient-orb-cyan" aria-hidden="true" />

      <div className="relative z-10 mx-auto w-full max-w-5xl px-6 py-10">
        {/* Hero — centered layout */}
        <motion.div
          className="mb-12 text-center"
          initial={false}
          animate={{ opacity: 1, y: 0 }}
        >
          {/* Eyebrow badge */}
          <motion.div
            className="ws-eyebrow mb-5"
            initial={false}
            animate={{ opacity: 1, scale: 1 }}
          >
            {t.workspace.home.eyebrow}
          </motion.div>

          <h1 className="text-foreground mb-3 text-4xl leading-[1.15] font-extrabold tracking-[-1.2px] sm:text-5xl md:text-[52px]">
            {t.workspace.home.titleLine1}
            <br />
            {t.workspace.home.titleLine2Prefix}{" "}
            <span
              style={{
                background: "linear-gradient(135deg, #6366f1, #ec4899)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              {t.workspace.home.titleLine2Brand}
            </span>{" "}
            {t.workspace.home.titleLine2Suffix}
          </h1>
          <p className="text-muted-foreground mx-auto mb-8 max-w-[480px] text-[15px] leading-relaxed">
            {t.workspace.home.subtitle}
          </p>

          {/* Centered input with send button */}
          <motion.div
            className="relative mx-auto max-w-[580px]"
            initial={false}
            animate={{ opacity: 1, y: 0 }}
          >
            <input
              ref={inputRef}
              style={glassStyle()}
              className={cn(
                "h-[52px] w-full rounded-2xl px-5 pr-14 text-sm shadow-xl outline-none",
                isDark
                  ? "text-white placeholder-slate-500"
                  : "text-slate-800 placeholder-slate-400",
              )}
              placeholder={t.workspace.home.inputPlaceholder}
              onKeyDown={(e) => {
                if (e.key === "Enter" && e.currentTarget.value.trim()) {
                  startFree();
                }
              }}
            />
            <button
              className="absolute top-1/2 right-2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-lg border-none bg-gradient-to-br from-[#6366f1] to-[#4f46e5] text-white shadow-[0_4px_12px_rgba(99,102,241,0.3)] transition-all duration-200 hover:scale-105 hover:shadow-[0_6px_20px_rgba(99,102,241,0.4)]"
              onClick={startFree}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </motion.div>
        </motion.div>

        {/* Workflow Grid */}
        <motion.div
          className="mb-10"
          initial={false}
          animate={{ opacity: 1, y: 0 }}
        >
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2.5 text-[17px] font-bold">
              <span className="ws-section-bar" />
              {t.workspace.home.expertAgents}
            </h2>
            <span
              onClick={() => router.push("/workspace/agents")}
              className="text-muted-foreground hover:text-primary cursor-pointer text-xs transition-colors"
            >
              {t.workspace.home.viewAll}
            </span>
          </div>
          <WorkflowGrid onSelect={startWorkflow} />
        </motion.div>

        {/* Recent Projects */}
        <motion.div initial={false} animate={{ opacity: 1, y: 0 }}>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2.5 text-[17px] font-bold">
              <span className="ws-section-bar" />
              {t.workspace.home.recentProjects}
            </h2>
            <span
              onClick={() => router.push("/workspace/agents")}
              className="text-muted-foreground hover:text-primary cursor-pointer text-xs transition-colors"
            >
              {t.workspace.home.viewAll}
            </span>
          </div>
          <ProjectList threads={threads ?? []} />
        </motion.div>
      </div>
    </div>
  );
}
