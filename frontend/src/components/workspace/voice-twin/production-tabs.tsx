"use client";

import { useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { ProductionTask } from "@/core/voice-twin/production-types";
import { cn } from "@/lib/utils";

import { DocToAudio } from "./doc-to-audio";
import { PptToVideo } from "./ppt-to-video";
import { ScriptToAudio } from "./script-to-audio";
import type { AudioFormat } from "./speed-format-controls";

type TabKey = "script-to-audio" | "doc-to-audio" | "ppt-to-video";

interface ProductionTabsProps {
  onTaskCreated?: (task: ProductionTask) => void;
}

export function ProductionTabs({ onTaskCreated }: ProductionTabsProps) {
  const { t } = useI18n();
  const vt = t.voiceTwin;
  const [activeTab, setActiveTab] = useState<TabKey>("script-to-audio");

  // Shared production state — selecting a voice in one tab now persists across
  // tabs (previously each tab held its own voiceId/speed/format and reset on
  // every switch).
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(1.0);
  const [format, setFormat] = useState<AudioFormat>("mp3");

  const tabs: { key: TabKey; label: string }[] = [
    {
      key: "script-to-audio",
      label: vt.scriptToAudio ?? "文稿转音频",
    },
    {
      key: "doc-to-audio",
      label: vt.docToAudio ?? "文档转音频",
    },
    {
      key: "ppt-to-video",
      label: vt.pptToVideo ?? "PPT 转视频",
    },
  ];

  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex gap-1 rounded-lg bg-black/5 p-1 dark:bg-white/5">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              "flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
              activeTab === tab.key
                ? "bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-white"
                : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content — hidden/show to preserve draft state */}
      <div className={cn(activeTab !== "script-to-audio" && "hidden")}>
        <ScriptToAudio
          onTaskCreated={onTaskCreated}
          voiceId={voiceId}
          onVoiceIdChange={setVoiceId}
          speed={speed}
          onSpeedChange={setSpeed}
          format={format}
          onFormatChange={setFormat}
        />
      </div>
      <div className={cn(activeTab !== "doc-to-audio" && "hidden")}>
        <DocToAudio
          onTaskCreated={onTaskCreated}
          voiceId={voiceId}
          onVoiceIdChange={setVoiceId}
          speed={speed}
          onSpeedChange={setSpeed}
          format={format}
          onFormatChange={setFormat}
        />
      </div>
      <div className={cn(activeTab !== "ppt-to-video" && "hidden")}>
        <PptToVideo
          onTaskCreated={onTaskCreated}
          voiceId={voiceId}
          onVoiceIdChange={setVoiceId}
        />
      </div>
    </div>
  );
}
