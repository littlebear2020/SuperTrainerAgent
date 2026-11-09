import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Upload,
  type LucideIcon,
} from "lucide-react";

import type { Translations } from "@/core/i18n/locales/types";

import type { ProductionTaskStatus } from "./production-types";
import type { VoiceTwinStatus } from "./types";

/** The voiceTwin slice of the translations object. */
type VoiceTwinMessages = Translations["voiceTwin"];

/**
 * Map a VoiceTwin (cloned voice) status to a localized label.
 * Previously voice-card.tsx / training-progress.tsx rendered the raw enum
 * literal ("uploading"/"training"/"ready"/"failed") in an otherwise all-Chinese
 * UI. Falls back to the raw status if no label is defined.
 */
export function voiceTwinStatusLabel(
  status: VoiceTwinStatus,
  vt: VoiceTwinMessages,
): string {
  switch (status) {
    case "uploading":
      return vt.statusUploading;
    case "training":
      return vt.statusTraining;
    case "ready":
      return vt.statusReady;
    case "failed":
      return vt.statusFailed;
    default:
      return status;
  }
}

/**
 * Map a ProductionTask status to a localized label.
 * Covers the 7 production states (pending/extracting/converting/synthesizing/
 * composing/ready/failed). The 5 intermediate states previously had no label.
 */
export function productionTaskStatusLabel(
  status: ProductionTaskStatus,
  vt: VoiceTwinMessages,
): string {
  switch (status) {
    case "pending":
      return vt.statusPending;
    case "extracting":
      return vt.statusExtracting;
    case "converting":
      return vt.statusConverting;
    case "synthesizing":
      return vt.statusSynthesizing;
    case "composing":
      return vt.statusComposing;
    case "ready":
      return vt.statusReady;
    case "failed":
      return vt.statusFailed;
    default:
      return status;
  }
}

// ── 状态样式（2026-10-06 合并批单一事实源）──────────────────────────────
// 修前 voice-card（STATUS_STYLE）与 training-progress（STATUS_CONFIG）逐字双份。

export interface VoiceStatusStyle {
  icon: LucideIcon;
  color: string;
  bg: string;
}

export const VOICE_STATUS_STYLE: Record<VoiceTwinStatus, VoiceStatusStyle> = {
  uploading: {
    icon: Upload,
    color: "text-blue-500",
    bg: "bg-blue-500/10",
  },
  training: {
    icon: Loader2,
    color: "text-amber-500",
    bg: "bg-amber-500/10",
  },
  ready: {
    icon: CheckCircle2,
    color: "text-emerald-500",
    bg: "bg-emerald-500/10",
  },
  failed: {
    icon: AlertCircle,
    color: "text-red-500",
    bg: "bg-red-500/10",
  },
};
