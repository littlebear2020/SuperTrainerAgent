/**
 * agent → 图标/配色单一事实源（2026-10-06 近重复合并批）。
 *
 * 修前 agent-card（AGENT_ICON_MAP）、workflow-grid（WORKFLOWS.color/icon）、
 * project-list（TYPE_MAP.badge/color）三处各持一份且已分叉（ppt-master
 * teal vs amber、course-assistant 图标/配色双轨）。收敛后以本表为准：
 * - ppt-master = teal（Q5 拍板，agent-card/workflow-grid 基准，project-list
 *   由 amber 改齐）
 * - course-assistant = sky + MessagesSquare（取 workflow-grid 口径：自由对话
 *   兜底语义，且与 course-video 的 indigo 区分；agent-card 原为 BookOpen+indigo）
 * - co-writer = violet + PenLine（原仅 workflow-grid 持有）
 * 未知名走 fallback（MessageSquare + indigo，agent-card 原兜底口径）。
 */

import {
  Clapperboard,
  Film,
  MessageSquare,
  MessagesSquare,
  Palette,
  PenLine,
  Presentation,
  type LucideIcon,
} from "lucide-react";

import { resolveAgentName } from "./retired-mapping";

export interface AgentAppearance {
  icon: LucideIcon;
  bg: string;
  text: string;
}

export const AGENT_APPEARANCE: Record<string, AgentAppearance> = {
  "course-video": {
    icon: Clapperboard,
    bg: "bg-indigo-100 dark:bg-indigo-500/10",
    text: "text-indigo-600 dark:text-indigo-400",
  },
  "vimax-video": {
    icon: Film,
    bg: "bg-rose-100 dark:bg-rose-500/10",
    text: "text-rose-600 dark:text-rose-400",
  },
  // 旧 5 壳（card-poster/case-article/design-specialist/knowledge-extraction/
  // video-clip）与 course-ppt 经 resolveAgentName 归一后命中以下两项。
  "creative-studio": {
    icon: Palette,
    bg: "bg-fuchsia-100 dark:bg-fuchsia-500/10",
    text: "text-fuchsia-600 dark:text-fuchsia-400",
  },
  "course-assistant": {
    icon: MessagesSquare,
    bg: "bg-sky-100 dark:bg-sky-500/10",
    text: "text-sky-600 dark:text-sky-400",
  },
  "ppt-master": {
    icon: Presentation,
    bg: "bg-teal-100 dark:bg-teal-500/10",
    text: "text-teal-600 dark:text-teal-400",
  },
  "co-writer": {
    icon: PenLine,
    bg: "bg-violet-100 dark:bg-violet-500/10",
    text: "text-violet-600 dark:text-violet-400",
  },
};

export const AGENT_APPEARANCE_FALLBACK: AgentAppearance = {
  icon: MessageSquare,
  bg: "bg-indigo-100 dark:bg-indigo-500/10",
  text: "text-indigo-600 dark:text-indigo-400",
};

/** 归一退役别名后取外观；未知名走 fallback。 */
export function agentAppearance(name: string): AgentAppearance {
  return AGENT_APPEARANCE[resolveAgentName(name)] ?? AGENT_APPEARANCE_FALLBACK;
}

/** 徽标复合类名（workflow-grid 卡片底色/project-list 标签共用形态）。 */
export function agentBadge(name: string): string {
  const a = agentAppearance(name);
  return `${a.bg} ${a.text}`;
}
