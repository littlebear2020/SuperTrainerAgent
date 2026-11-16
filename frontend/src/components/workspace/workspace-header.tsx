"use client";

import { LayoutDashboard } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";

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
  const { resolvedTheme } = useTheme();
  const pathname = usePathname();
  const brandMark =
    resolvedTheme === "dark"
      ? "/images/trainer-ai-mark-inverse.svg"
      : "/images/trainer-ai-mark.svg";
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
              aria-label="Trainer AI"
              className="block pt-1 group-hover/workspace-header:hidden"
            >
              <img
                src={brandMark}
                alt=""
                aria-hidden="true"
                className="h-8 w-8"
              />
            </Link>
            <SidebarTrigger className="hidden pl-2 group-hover/workspace-header:block" />
          </div>
        ) : (
          <div className="flex items-center justify-between gap-2">
            <Link href="/" className="ml-2 flex items-center gap-3">
              <img
                src={brandMark}
                alt=""
                aria-hidden="true"
                className="h-[34px] w-[34px] shrink-0"
              />
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
            // 首页只精确匹配 /workspace；Agent 新会话等深链不误亮首页。
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
