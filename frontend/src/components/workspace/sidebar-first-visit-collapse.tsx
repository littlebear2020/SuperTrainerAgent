"use client";

import { useEffect } from "react";

import { useSidebar } from "@/components/ui/sidebar";

/**
 * DSN-015（2026-09-14）：窄屏首访侧栏默认收起 + cookie 持久化。
 *
 * 规则：sidebar_state cookie 未设置过（首访）且视口 ≤1024px → 收起；
 * cookie 已存在则一切以 cookie 为准——不劫持用户手动选择（展开/收起都由
 * SidebarProvider.setOpen 回写 cookie）。
 */
export function SidebarFirstVisitCollapse({
  cookieWasSet,
}: {
  cookieWasSet: boolean;
}) {
  const { setOpen } = useSidebar();
  useEffect(() => {
    if (cookieWasSet) return;
    if (window.matchMedia("(max-width: 1024px)").matches) {
      setOpen(false); // 经 provider 写 cookie——手动展开后刷新保持
    }
  }, [cookieWasSet, setOpen]);
  return null;
}
