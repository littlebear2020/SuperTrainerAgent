"use client";

import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * 空态三件套统一（D 级 UI 收敛，2026-09-14）：图标 + 文案（+描述）+ 可选 CTA。
 * 应用于 Chats 空态（DSN-014）、Writer 搜索空态（DSN-005）、Gallery 空态——
 * 三处此前各自实现，形态漂移。主题双适配（slate 中性色 + dark: 变体）。
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-4 py-16 text-center",
        className,
      )}
    >
      {Icon ? (
        <Icon className="h-10 w-10 text-slate-300 dark:text-slate-600" />
      ) : null}
      <div>
        <p className="text-sm text-slate-500 dark:text-slate-400">{title}</p>
        {description ? (
          <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
            {description}
          </p>
        ) : null}
      </div>
      {action}
    </div>
  );
}
