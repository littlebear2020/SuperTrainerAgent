import type { LucideIcon } from "lucide-react";
import {
  BotIcon,
  Boxes,
  FileText,
  GaugeIcon,
  LightbulbIcon,
  Mic,
  MessagesSquare,
  Video,
} from "lucide-react";

/**
 * DSN-002（2026-09-14）：工作台页面路由表——**单一事实源**。
 *
 * 侧栏（workspace-nav-chat-list）与 ⌘K（command-palette）页面组均从本表 map 渲染，
 * 不另写第二份。`includeInPalette`：⌘K 页面名单按 2026-09-14 产品拍板为默认 6 页
 * （Video/Writing/Gallery/Dubbing/Assets/Console）；Chats 有专属 ⌘⇧N 快捷动作，
 * 不重复进页面组。
 */
export interface WorkspaceNavItem {
  /** t.sidebar 的键——文案仍走 i18n（zh/en 双语）。 */
  labelKey:
    | "chats"
    | "agents"
    | "gallery"
    | "voiceTwin"
    | "writer"
    | "videoGen"
    | "assets"
    | "consoleAdmin";
  href: string;
  icon: LucideIcon;
  /** active 判定：exact=pathname === href；prefix=pathname.startsWith(matchPrefix ?? href) */
  match: "exact" | "prefix";
  /** prefix 匹配前缀（缺省用 href） */
  matchPrefix?: string;
  /** 侧栏 Beta 徽标（⌘K 不需要徽标） */
  badge?: "beta";
  /** console 标签随角色变化（regular: consoleSelf / admin: consoleAdmin） */
  labelRole?: "console";
  /** 是否进 ⌘K 页面组（2026-09-14 拍板 6 页） */
  includeInPalette?: boolean;
}

export const WORKSPACE_NAV_ITEMS: WorkspaceNavItem[] = [
  {
    labelKey: "chats",
    href: "/workspace/chats",
    icon: MessagesSquare,
    match: "exact",
  },
  {
    // 2026-09-22 工作台恢复：加回「智能体」入口（b68f3a7e4 反向）。
    // 不设 includeInPalette——⌘K 维持 09-14 拍板 6 页口径。
    labelKey: "agents",
    href: "/workspace/agents",
    icon: BotIcon,
    match: "prefix",
  },
  {
    labelKey: "gallery",
    href: "/workspace/gallery",
    icon: LightbulbIcon,
    match: "exact",
    includeInPalette: true,
  },
  {
    labelKey: "voiceTwin",
    href: "/workspace/twins/voice",
    icon: Mic,
    match: "prefix",
    matchPrefix: "/workspace/twins",
    badge: "beta",
    includeInPalette: true,
  },
  {
    labelKey: "writer",
    href: "/workspace/writer",
    icon: FileText,
    match: "prefix",
    badge: "beta",
    includeInPalette: true,
  },
  {
    labelKey: "videoGen",
    href: "/workspace/video-gen",
    icon: Video,
    match: "prefix",
    badge: "beta",
    includeInPalette: true,
  },
  {
    labelKey: "assets",
    href: "/workspace/assets",
    icon: Boxes,
    match: "prefix",
    badge: "beta",
    includeInPalette: true,
  },
  {
    labelKey: "consoleAdmin",
    href: "/workspace/console",
    icon: GaugeIcon,
    match: "prefix",
    labelRole: "console",
    includeInPalette: true,
  },
];

export function isNavItemActive(
  item: WorkspaceNavItem,
  pathname: string,
): boolean {
  if (item.match === "exact") return pathname === item.href;
  return pathname.startsWith(item.matchPrefix ?? item.href);
}

/** ⌘K 页面组（DSN-002 默认 6 页，见文件头注释）。 */
export const PALETTE_NAV_ITEMS: WorkspaceNavItem[] = WORKSPACE_NAV_ITEMS.filter(
  (i) => i.includeInPalette,
);
