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
// 2026-11-16：creative-studio 收敛为视觉生图入口，剪辑与知识萃取关键词已移除
// （不再路由到它）；ppt-master 承接 course-ppt 关键词（09-11 退役）；写作类由
// co-writer 承接。无命中返回 null（2026-11-16.2 course-assistant 退役）。
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
    // 2026-11-16 收敛为视觉生图：只保留视觉关键词，剪辑/知识萃取不再命中本入口。
    agent: "creative-studio",
    keywords: [
      "知识卡片",
      "海报",
      "宣传图",
      "信息图",
      "卡片设计",
      "视觉设计",
      "品牌视觉",
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
      <div className="relative z-10 mx-auto w-full max-w-6xl px-4 py-3 sm:px-6 sm:pt-3 sm:pb-8">
        <header className="mb-8 grid items-center gap-8 md:grid-cols-[minmax(0,1.35fr)_minmax(240px,.65fr)] md:gap-12">
          <div className="text-center md:translate-y-7 md:text-left">
            <div className="mb-3 flex items-center justify-center gap-2 text-[11px] font-bold tracking-[0.14em] text-indigo-600 md:justify-start">
              <span aria-hidden="true" className="h-px w-5 bg-current" />
              {t.workspace.home.heroKicker}
            </div>
            <h1 className="text-foreground text-4xl leading-tight font-extrabold tracking-[-0.045em] sm:text-5xl">
              {t.workspace.home.heroTitle}
            </h1>
            <p className="text-muted-foreground mt-4 text-sm leading-7 sm:text-base">
              {t.workspace.home.createHeading}
            </p>
          </div>
          <div className="relative mx-auto flex h-64 w-full max-w-sm translate-y-6 items-end justify-center sm:h-72 md:h-64 md:translate-y-16">
            <img
              src="/course-samples/teacher-A-half.png"
              alt={t.workspace.home.teacherAlt}
              className="h-52 w-auto max-w-full origin-bottom scale-110 object-contain sm:h-60"
            />
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
