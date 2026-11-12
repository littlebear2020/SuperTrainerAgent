"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { BetaBadge } from "@/components/workspace/beta-badge";
import { useIsConsoleAdmin } from "@/core/console/hooks";
import { useI18n } from "@/core/i18n/hooks";
import {
  isNavItemActive,
  WORKSPACE_NAV_ITEMS,
} from "@/core/workspace/nav-routes";

export function WorkspaceNavChatList() {
  const { t } = useI18n();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // Same role source the console itself uses, so the label can never disagree
  // with the scope of the data behind it.
  const isConsoleAdmin = useIsConsoleAdmin();
  return (
    <SidebarGroup className="pt-1">
      <SidebarGroupLabel className="text-muted-foreground text-[10px] font-semibold tracking-[1.2px] uppercase">
        {t.sidebar.mySpace}
      </SidebarGroupLabel>
      <SidebarMenu>
        {/* DSN-002（2026-09-14）：从 nav-routes 单一事实源 map 渲染（⌘K 同源）。 */}
        {WORKSPACE_NAV_ITEMS.map((item) => {
          const label =
            item.labelRole === "console"
              ? isConsoleAdmin
                ? t.sidebar.consoleAdmin
                : t.sidebar.consoleSelf
              : t.sidebar[item.labelKey];
          const Icon = item.icon;
          return (
            <SidebarMenuItem key={item.href}>
              <SidebarMenuButton
                isActive={isNavItemActive(item, pathname, searchParams)}
                asChild
              >
                <Link className="text-muted-foreground" href={item.href}>
                  <Icon size={16} />
                  <span>{label}</span>
                  {item.badge === "beta" && <BetaBadge />}
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          );
        })}
      </SidebarMenu>
    </SidebarGroup>
  );
}
