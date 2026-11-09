"use client";

/**
 * 灵感来源徽章（official / ugc）——2026-10-07 文件级拆分批自
 * gallery/page.tsx 逐字上提（详情弹窗与页面卡片双端使用；依赖 isDark prop）。
 */

import { cn } from "@/lib/utils";

/** Source type badge (official / ugc) */
export function SourceBadge({
  sourceType,
  isDark,
}: {
  sourceType: "official" | "ugc";
  isDark: boolean;
}) {
  if (sourceType === "ugc") {
    return (
      <span
        className={cn(
          "rounded-md px-1.5 py-0.5 text-[8px] font-bold",
          isDark
            ? "bg-emerald-500/20 text-emerald-300"
            : "bg-emerald-500/10 text-emerald-600",
        )}
      >
        社区
      </span>
    );
  }
  return (
    <span
      className={cn(
        "rounded-md px-1.5 py-0.5 text-[8px] font-bold",
        isDark
          ? "bg-blue-500/20 text-blue-300"
          : "bg-blue-500/10 text-blue-600",
      )}
    >
      官方
    </span>
  );
}
