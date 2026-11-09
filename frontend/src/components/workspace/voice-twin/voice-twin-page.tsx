"use client";

import { Mic, Video } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { BetaBadge } from "../beta-badge";

import { ProductionHistory } from "./production-history";
import { ProductionTabs } from "./production-tabs";
import { UploadPanel } from "./upload-panel";
import { VoiceList } from "./voice-list";

type TopTab = "voice" | "production";

// ── Component ─────────────────────────────────────────────────────────────

export function VoiceTwinPage() {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  const isDark = mounted ? resolvedTheme === "dark" : false;

  const { t } = useI18n();
  const vt = t.voiceTwin;

  const [activeTab, setActiveTab] = useState<TopTab>("voice");
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);

  const topTabs: { key: TopTab; label: string; icon: typeof Mic }[] = [
    {
      key: "voice",
      label: vt.tabVoiceManagement ?? "声线管理",
      icon: Mic,
    },
    {
      key: "production",
      label: vt.tabProduction ?? "课件配音工具箱",
      icon: Video,
    },
  ];

  return (
    <div className="scrollbar-none flex size-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-5xl space-y-6">
        {/* Header */}
        <div>
          <h1
            className={cn(
              "flex items-center gap-2 text-lg font-bold",
              isDark ? "text-white" : "text-slate-800",
            )}
          >
            <Mic className="h-5 w-5 text-indigo-500" />
            {vt.title}
            <BetaBadge />
          </h1>
          <p className="mt-1 text-xs text-slate-400">{vt.description}</p>
        </div>

        {/* Top-level tabs */}
        <div className="flex gap-1 rounded-lg bg-black/5 p-1 dark:bg-white/5">
          {topTabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-1.5 rounded-md px-4 py-2 text-sm font-medium transition-colors",
                  activeTab === tab.key
                    ? "bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-white"
                    : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200",
                )}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Tab A: Voice Management */}
        <div className={cn(activeTab !== "voice" && "hidden")}>
          <div className="flex flex-col gap-4 lg:flex-row">
            <div className="lg:flex-[2]">
              <UploadPanel />
            </div>
            <div className="lg:flex-[3]">
              <VoiceList />
            </div>
          </div>
        </div>

        {/* Tab B: Production Studio */}
        <div className={cn(activeTab !== "production" && "hidden")}>
          <div className="space-y-4">
            <ProductionTabs
              onTaskCreated={() => setHistoryRefreshKey((k) => k + 1)}
            />
            <ProductionHistory key={historyRefreshKey} />
          </div>
        </div>
      </div>
    </div>
  );
}
