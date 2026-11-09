"use client";

import { AlertCircle, CheckCircle2, Loader2, Upload } from "lucide-react";
import { useEffect } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { useVoiceTwinStatus } from "@/core/voice-twin/hooks";
import {
  voiceTwinStatusLabel,
  VOICE_STATUS_STYLE,
} from "@/core/voice-twin/status-labels";
import type { VoiceTwinStatus } from "@/core/voice-twin/types";
import { cn } from "@/lib/utils";

// ── Status config ────────────────────────────────────────────────────────

// ── Component ────────────────────────────────────────────────────────────

interface TrainingProgressProps {
  voiceId: string;
  onComplete?: () => void;
  onError?: (error: string) => void;
}

export function TrainingProgress({
  voiceId,
  onComplete,
  onError,
}: TrainingProgressProps) {
  const { data, error } = useVoiceTwinStatus(voiceId);
  const { t } = useI18n();
  const vt = t.voiceTwin;

  // Fire callbacks in an effect to avoid React state-update-during-render.
  useEffect(() => {
    if (data?.status === "ready" && onComplete) {
      onComplete();
    }
    if (data?.status === "failed" && onError) {
      onError(data.error_message ?? "Training failed");
    }
  }, [data?.status, data?.error_message, onComplete, onError]);

  // ── Error state (network / query error) ──────────────────────────────
  if (error) {
    return (
      <div className="flex items-center gap-2 rounded-xl bg-red-500/10 p-3 text-red-400">
        <AlertCircle className="h-4 w-4 shrink-0" />
        <span className="text-xs font-bold">{error.message}</span>
      </div>
    );
  }

  // ── No data yet ──────────────────────────────────────────────────────
  if (!data) {
    return null;
  }

  // ── Normal status display ────────────────────────────────────────────
  const config = VOICE_STATUS_STYLE[data.status];
  const Icon = config.icon;
  const spins = data.status === "uploading" || data.status === "training";

  return (
    <div className={cn("flex items-center gap-2 rounded-xl p-3", config.bg)}>
      <Icon
        className={cn(
          "h-4 w-4 shrink-0",
          config.color,
          spins && "animate-spin",
        )}
      />
      <span className={cn("text-xs font-bold", config.color)}>
        {data.status ? voiceTwinStatusLabel(data.status, vt) : ""}
      </span>
      {data.error_message && (
        <span className="text-xs text-red-400">{data.error_message}</span>
      )}
    </div>
  );
}
