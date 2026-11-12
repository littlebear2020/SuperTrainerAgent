import type { LucideIcon } from "lucide-react";
import { FolderIcon, GaugeIcon, Video } from "lucide-react";

import {
  parseVideoAssetsRoute,
  VIDEO_ASSETS_PATH,
  type VideoAssetsTab,
} from "./video-assets-route";

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
    | "myWorks"
    | "myAssets"
    | "consoleAdmin";
  href: string;
  icon: LucideIcon;
  /** active 判定：exact=pathname === href；prefix=pathname.startsWith(matchPrefix ?? href) */
  match: "exact" | "prefix";
  /** prefix 匹配前缀（缺省用 href） */
  matchPrefix?: string;
  /** 视频作品/资产需要同时匹配规范化 tab。 */
  tab?: VideoAssetsTab;
  /** 侧栏 Beta 徽标（⌘K 不需要徽标） */
  badge?: "beta";
  /** console 标签随角色变化（regular: consoleSelf / admin: consoleAdmin） */
  labelRole?: "console";
  /** 是否进 ⌘K 页面组 */
  includeInPalette?: boolean;
}

export const WORKSPACE_NAV_ITEMS: WorkspaceNavItem[] = [
  {
    labelKey: "myWorks",
    href: VIDEO_ASSETS_PATH,
    icon: Video,
    match: "prefix",
    tab: "works",
    badge: "beta",
    includeInPalette: true,
  },
  {
    labelKey: "myAssets",
    href: `${VIDEO_ASSETS_PATH}?tab=assets`,
    icon: FolderIcon,
    match: "prefix",
    tab: "assets",
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
  search: string | { get(name: string): string | null } = "",
): boolean {
  if (item.tab) {
    if (pathname === VIDEO_ASSETS_PATH) {
      return parseVideoAssetsRoute(search).tab === item.tab;
    }
    if (pathname.startsWith(`${VIDEO_ASSETS_PATH}/`)) {
      return item.tab === "works";
    }
    return false;
  }
  if (item.match === "exact") return pathname === item.href;
  return pathname.startsWith(item.matchPrefix ?? item.href);
}

export const PALETTE_NAV_ITEMS: WorkspaceNavItem[] = WORKSPACE_NAV_ITEMS.filter(
  (i) => i.includeInPalette,
);
