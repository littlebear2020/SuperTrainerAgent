"use client";

import { Mic } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { useVoiceTwins } from "@/core/voice-twin/hooks";

import { VoiceCard } from "./voice-card";

// ── Component ─────────────────────────────────────────────────────────────

export function VoiceList() {
  const { t } = useI18n();
  const vt = t.voiceTwin;
  const { data: voices, isLoading, error } = useVoiceTwins();

  // ── Loading state ────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 2 }).map((_, i) => (
          <div
            key={i}
            className="h-28 animate-pulse rounded-2xl bg-slate-200/30 dark:bg-slate-700/20"
          />
        ))}
      </div>
    );
  }

  // ── Error state ──────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-600 dark:border-red-500/20 dark:bg-red-500/10">
        {error.message}
      </div>
    );
  }

  // ── Empty state ──────────────────────────────────────────────────────────
  if (!voices || voices.length === 0) {
    return (
      <div className="flex h-32 flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-slate-300/60 dark:border-slate-600/40">
        <Mic className="h-6 w-6 text-slate-300 dark:text-slate-600" />
        <p className="text-xs text-slate-400">{vt.noVoices}</p>
      </div>
    );
  }

  // ── Normal state ─────────────────────────────────────────────────────────
  return (
    <div className="space-y-3">
      {voices.map((voice) => (
        <VoiceCard key={voice.id} voice={voice} />
      ))}
    </div>
  );
}
