"use client";

import { MessageSquare } from "lucide-react";
import Link from "next/link";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useState } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { EmptyState } from "@/components/workspace/empty-state";
import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import { useThreads } from "@/core/threads/hooks";
import { pathOfThread, titleOfThread } from "@/core/threads/utils";
import { formatTimeAgo } from "@/core/utils/datetime";
import { cn } from "@/lib/utils";

export default function ChatsPage() {
  const { t } = useI18n();
  const { data: threads } = useThreads();
  const [search, setSearch] = useState("");
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const { glassStyle } = useGlassStyle();

  useEffect(() => {
    document.title = `${t.pages.chats} - ${t.pages.appName}`;
  }, [t.pages.chats, t.pages.appName]);

  const filteredThreads = useMemo(() => {
    return threads?.filter((thread) => {
      return titleOfThread(thread).toLowerCase().includes(search.toLowerCase());
    });
  }, [threads, search]);

  return (
    <div className="scrollbar-none flex size-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-6xl space-y-8">
        {/* Header */}
        <div>
          <h1
            className={cn(
              "flex items-center gap-2 text-lg font-extrabold",
              isDark ? "text-white" : "text-slate-800",
            )}
          >
            <MessageSquare className="h-5 w-5 text-indigo-500" />
            {t.pages.chats}
          </h1>
        </div>

        {/* Search Input */}
        <div className="relative">
          <svg
            className={cn(
              "absolute top-1/2 left-4 h-4 w-4 -translate-y-1/2",
              isDark ? "text-slate-500" : "text-slate-400",
            )}
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={2}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
            />
          </svg>
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t.chats.searchChats}
            autoFocus
            style={glassStyle()}
            className={cn(
              "w-full rounded-2xl py-3.5 pr-6 pl-11 text-xs shadow-xl transition-all duration-300 outline-none",
              isDark
                ? "text-white placeholder-slate-500"
                : "text-slate-800 placeholder-slate-400",
            )}
          />
        </div>

        {/* Thread List */}
        {filteredThreads && filteredThreads.length > 0 ? (
          <div className="space-y-3">
            {filteredThreads.map((thread) => (
              <Link key={thread.thread_id} href={pathOfThread(thread)}>
                <div
                  style={glassStyle()}
                  className="interactive-glass group flex items-center gap-4 rounded-2xl p-4 transition-transform hover:scale-[1.01]"
                >
                  <div
                    className={cn(
                      "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl",
                      isDark
                        ? "bg-white/5 text-indigo-400"
                        : "bg-indigo-500/10 text-indigo-600",
                    )}
                  >
                    <MessageSquare className="h-4 w-4" />
                  </div>
                  <div className="relative z-[1] min-w-0 flex-1">
                    <div
                      className={cn(
                        "truncate text-sm font-semibold",
                        isDark ? "text-white" : "text-slate-800",
                      )}
                    >
                      {titleOfThread(thread)}
                    </div>
                    {thread.updated_at && (
                      <div
                        className={cn(
                          "mt-1 text-xs",
                          isDark ? "text-slate-400" : "text-slate-500",
                        )}
                      >
                        {formatTimeAgo(thread.updated_at)}
                      </div>
                    )}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        ) : (
          /* DSN-014（2026-09-14）：空态收敛到 EmptyState + 「发起新对话」CTA
             （对齐 Writing 空态三件套；搜索无结果态保持文案、不重复给 CTA）。 */
          <EmptyState
            icon={MessageSquare}
            title={search ? `没有找到"${search}"相关的对话` : "暂无对话记录"}
            description={search ? undefined : "开始你的第一段对话"}
            action={
              search ? undefined : (
                <Link
                  href={pathOfThread("new")}
                  className="rounded-full bg-indigo-500 px-5 py-2 text-xs font-bold text-white transition-colors hover:bg-indigo-600"
                >
                  发起新对话
                </Link>
              )
            }
          />
        )}
      </div>
    </div>
  );
}
