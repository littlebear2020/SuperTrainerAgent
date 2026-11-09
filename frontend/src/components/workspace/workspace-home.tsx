"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { useThreads } from "@/core/threads/hooks";

import type { HomeCourseItem } from "./home-course-catalog";
import { HomeCourseEntry } from "./home-course-entry";
import { ProjectList } from "./project-list";
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
  const { data: threads } = useThreads();
  const { t } = useI18n();
  const [createTaskType, setCreateTaskType] =
    useState<InitialVideoTaskType | null>(null);

  const handleSelect = (item: HomeCourseItem) => {
    if (item.kind === "agent") {
      router.push(`/workspace/agents/${item.agentName}/chats/new`);
      return;
    }
    setCreateTaskType(item.initialTaskType);
  };

  return (
    <div className="relative flex h-full flex-col overflow-y-auto">
      <div className="ws-ambient" aria-hidden="true" />
      <div className="ws-ambient-orb-cyan" aria-hidden="true" />
      <div className="relative z-10 mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
        <header className="mb-8 text-center">
          <h1 className="text-foreground text-3xl leading-tight font-extrabold tracking-tight sm:text-5xl">
            {t.workspace.home.heroTitle}
          </h1>
          <p className="text-muted-foreground mt-3 text-sm sm:text-base">
            {t.workspace.home.createHeading}
          </p>
        </header>
        <HomeCourseEntry onSelect={handleSelect} />
        <section className="mt-10">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2.5 text-[17px] font-bold">
              <span className="ws-section-bar" />
              {t.workspace.home.recentProjects}
            </h2>
            <button
              type="button"
              onClick={() => router.push("/workspace/chats")}
              className="text-muted-foreground hover:text-primary text-xs transition-colors"
            >
              {t.workspace.home.viewAll}
            </button>
          </div>
          <ProjectList threads={threads ?? []} />
        </section>
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
