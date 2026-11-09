"use client";

import { BoltIcon, LayoutDashboard } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export function WorkspaceHeader({ className }: { className?: string }) {
  const { t } = useI18n();
  const { state } = useSidebar();
  const pathname = usePathname();
  return (
    <>
      <div
        className={cn(
          "group/workspace-header flex h-12 flex-col justify-center",
          className,
        )}
      >
        {state === "collapsed" ? (
          <div className="group-has-data-[collapsible=icon]/sidebar-wrapper:-translate-y flex w-full cursor-pointer items-center justify-center">
            <Link
              href="/"
              className="block pt-1 group-hover/workspace-header:hidden"
            >
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-[#6366f1] to-[#ec4899] shadow-[0_4px_14px_rgba(99,102,241,0.5)]">
                <BoltIcon className="h-3.5 w-3.5 text-white" />
              </div>
            </Link>
            <SidebarTrigger className="hidden pl-2 group-hover/workspace-header:block" />
          </div>
        ) : (
          <div className="flex items-center justify-between gap-2">
            <Link href="/" className="ml-2 flex items-center gap-3">
              <div className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-[#6366f1] to-[#ec4899] shadow-[0_4px_14px_rgba(99,102,241,0.5)]">
                <BoltIcon className="h-3.5 w-3.5 text-white" />
              </div>
              <div className="flex flex-col">
                <span
                  className="text-[17px] font-bold tracking-[-0.5px]"
                  style={{
                    background: "linear-gradient(135deg, #6366f1, #ec4899)",
                    WebkitBackgroundClip: "text",
                    WebkitTextFillColor: "transparent",
                  }}
                >
                  Trainer AI
                </span>
                <span className="text-muted-foreground text-[10.5px] font-medium">
                  Super Agent
                </span>
              </div>
            </Link>
            <SidebarTrigger />
          </div>
        )}
      </div>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton
            // 2026-09-22 工作台恢复：回退精确匹配（37bb1563e 反向）——
            // agent 聊天路由归「智能体」入口高亮，工作台只认 /workspace 本身。
            isActive={pathname === "/workspace" || pathname === "/workspace/"}
            asChild
          >
            <Link className="text-muted-foreground" href="/workspace">
              <LayoutDashboard size={16} />
              <span>{t.sidebar.dashboard}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </>
  );
}
