"use client";

import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Trash2,
  Upload,
  Wand2,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  buildConfirmOptions,
  useConfirm,
} from "@/components/workspace/dialog-provider";
import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import { getDemoAudioURL } from "@/core/voice-twin/api";
import { useDeleteVoiceTwin } from "@/core/voice-twin/hooks";
import { VOICE_STATUS_STYLE } from "@/core/voice-twin/status-labels";
import { voiceTwinStatusLabel } from "@/core/voice-twin/status-labels";
import type { VoiceTwin } from "@/core/voice-twin/types";
import { cn } from "@/lib/utils";

import { AudioPlayer } from "./audio-player";
import { SynthesizeDialog } from "./synthesize-dialog";
import { TrainingProgress } from "./training-progress";

// ── Status style mapping ──────────────────────────────────────────────────

// ── Component ─────────────────────────────────────────────────────────────

interface VoiceCardProps {
  voice: VoiceTwin;
}

export function VoiceCard({ voice }: VoiceCardProps) {
  const { t } = useI18n();
  const vt = t.voiceTwin;
  const { glassStyle } = useGlassStyle();
  const deleteMutation = useDeleteVoiceTwin();

  const [showSynthesize, setShowSynthesize] = useState(false);
  const confirm = useConfirm();

  const statusConfig = VOICE_STATUS_STYLE[voice.status];
  const StatusIcon = statusConfig.icon;

  const isProcessing =
    voice.status === "uploading" || voice.status === "training";

  const handleDelete = async () => {
    const confirmed = await confirm(
      buildConfirmOptions(vt.deleteConfirm, { variant: "destructive" }),
    );
    if (confirmed) {
      deleteMutation.mutate(voice.id, {
        onError: (err) => {
          toast.error(
            `删除音色失败：${err instanceof Error ? err.message : "未知错误"}`,
          );
        },
      });
    }
  };

  return (
    <>
      <div className="rounded-2xl p-4" style={glassStyle()}>
        {/* Row 1: voice name + status badge */}
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold">{voice.voice_name}</span>
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
              statusConfig.bg,
              statusConfig.color,
            )}
          >
            <StatusIcon
              className={cn("h-3 w-3", isProcessing && "animate-spin")}
            />
            {voiceTwinStatusLabel(voice.status, vt)}
          </span>
        </div>

        {/* Row 2: model type + duration */}
        <div className="mt-1 flex items-center gap-2 text-xs text-slate-400">
          <span>
            {vt.modelType}: {voice.model_type}
          </span>
          {voice.source_audio_duration != null && (
            <span>
              {vt.durationLabel}: {voice.source_audio_duration.toFixed(1)}s
            </span>
          )}
        </div>

        {/* Conditional: training/uploading progress */}
        {isProcessing && <TrainingProgress voiceId={voice.id} />}

        {/* Conditional: failed error message */}
        {voice.status === "failed" && voice.error_message && (
          <p className="mt-2 text-xs text-red-400">{voice.error_message}</p>
        )}

        {/* Conditional: ready + demo on disk — demo audio player */}
        {voice.status === "ready" && voice.demo_available && (
          <div className="mt-3">
            <AudioPlayer src={getDemoAudioURL(voice.id)} />
          </div>
        )}

        {/* Action buttons row */}
        <div className="mt-3 flex justify-end gap-2">
          {voice.status === "ready" && (
            <button
              type="button"
              onClick={() => setShowSynthesize(true)}
              className={cn(
                "inline-flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                "bg-indigo-500/10 text-indigo-500 hover:bg-indigo-500/20",
              )}
            >
              <Wand2 className="h-3.5 w-3.5" />
              {vt.synthesize}
            </button>
          )}
          <button
            type="button"
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
            className={cn(
              "inline-flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
              "bg-red-500/10 text-red-500 hover:bg-red-500/20",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            <Trash2 className="h-3.5 w-3.5" />
            {deleteMutation.isPending ? vt.deleting : vt.deleteButton}
          </button>
        </div>
      </div>

      {/* Synthesize dialog — always rendered, controlled by showSynthesize */}
      <SynthesizeDialog
        voiceId={voice.id}
        voiceName={voice.voice_name}
        open={showSynthesize}
        onClose={() => setShowSynthesize(false)}
      />
    </>
  );
}
