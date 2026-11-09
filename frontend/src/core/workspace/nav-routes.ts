import type { LucideIcon } from "lucide-react";
import { GaugeIcon, Video } from "lucide-react";

/**
 * 工作台可见页面路由表——侧栏与 ⌘K 页面组的单一事实源。
 * 隐藏入口的 App Router 路由与深链保留，不在此表中展示。
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
  /** 是否进 ⌘K 页面组 */
  includeInPalette?: boolean;
}

export const WORKSPACE_NAV_ITEMS: WorkspaceNavItem[] = [
  {
    labelKey: "videoGen",
    href: "/workspace/video-gen",
    icon: Video,
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

export const PALETTE_NAV_ITEMS: WorkspaceNavItem[] = WORKSPACE_NAV_ITEMS.filter(
  (i) => i.includeInPalette,
);
