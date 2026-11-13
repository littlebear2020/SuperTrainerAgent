"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { useI18n } from "@/core/i18n/hooks";

import type { HomeCourseItem } from "./home-course-catalog";
import { HomeCourseEntry } from "./home-course-entry";
import { useMediaQuery } from "./use-media-query";
import {
  CreateTaskDialog,
  type InitialVideoTaskType,
} from "./video-gen/video-gen-create-dialog";

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
  const { t } = useI18n();
  const [createTaskType, setCreateTaskType] =
    useState<InitialVideoTaskType | null>(null);
  const coarsePointer = useMediaQuery("(pointer: coarse)");
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const ambientMotionEnabled = !coarsePointer && !reducedMotion;

  const handleSelect = (item: HomeCourseItem) => {
    if (item.kind === "agent") {
      router.push(`/workspace/agents/${item.agentName}/chats/new`);
      return;
    }
    setCreateTaskType(item.initialTaskType);
  };

  return (
    <div className="relative flex h-full flex-col overflow-y-auto">
      {ambientMotionEnabled ? (
        <>
          <div className="ws-ambient" aria-hidden="true" />
          <div className="ws-ambient-orb-cyan" aria-hidden="true" />
        </>
      ) : null}
      <div className="relative z-10 mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
        <header className="mb-8 grid items-end gap-8 md:grid-cols-[minmax(0,1.35fr)_minmax(240px,.65fr)] md:gap-12">
          <div className="text-center md:text-left">
            <div className="mb-3 flex items-center justify-center gap-2 text-[11px] font-bold tracking-[0.14em] text-indigo-600 md:justify-start">
              <span aria-hidden="true" className="h-px w-5 bg-current" />
              YOUR CONTENT STUDIO
            </div>
            <h1 className="text-foreground text-4xl leading-tight font-extrabold tracking-[-0.045em] sm:text-5xl">
              {t.workspace.home.heroTitle}
            </h1>
            <p className="text-muted-foreground mt-4 text-sm leading-7 sm:text-base">
              {t.workspace.home.createHeading}
            </p>
          </div>
          <div className="relative mx-auto flex h-64 w-full max-w-sm items-end justify-center sm:h-72">
            <img
              src="/course-samples/teacher-A-half.png"
              alt={t.workspace.home.teacherAlt}
              className="h-64 w-auto max-w-full object-contain sm:h-72"
            />
            <span className="absolute right-2 bottom-3 flex items-center gap-1.5 rounded-full border border-indigo-100 bg-white/90 px-3 py-1.5 text-[10px] font-medium text-slate-600 shadow-sm">
              <span
                aria-hidden="true"
                className="size-1.5 rounded-full bg-teal-400"
              />
              {t.workspace.home.teacherNote}
            </span>
          </div>
        </header>
        <HomeCourseEntry onSelect={handleSelect} />
      </div>
      {createTaskType ? (
        <CreateTaskDialog
          open={true}
          initialTaskType={createTaskType}
          onOpenChange={(open) => {
            if (!open) setCreateTaskType(null);
          }}
        />
      ) : null}
    </div>
  );
}
