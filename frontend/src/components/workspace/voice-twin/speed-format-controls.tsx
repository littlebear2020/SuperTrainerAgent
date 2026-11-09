"use client";

import { useI18n } from "@/core/i18n/hooks";

export type AudioFormat = "mp3" | "wav" | "ogg";

interface SpeedFormatControlsProps {
  speed: number;
  format: AudioFormat;
  onSpeedChange: (speed: number) => void;
  onFormatChange: (format: AudioFormat) => void;
  className?: string;
}

/**
 * Shared speed-ratio + output-format controls. Extracted from the 3 places it
 * was copy-pasted (doc-to-audio preview/edit, script-to-audio). synthesize-dialog
 * has a vertical variant that is intentionally NOT migrated here.
 */
export function SpeedFormatControls({
  speed,
  format,
  onSpeedChange,
  onFormatChange,
  className,
}: SpeedFormatControlsProps) {
  const { t } = useI18n();
  const vt = t.voiceTwin;

  return (
    <div className={className ?? "mb-3 flex gap-3"}>
      <div className="flex-1">
        <label className="mb-1 block text-xs text-slate-500">
          {vt.speedRatio ?? "语速"} ({speed.toFixed(1)}x)
        </label>
        <input
          type="range"
          min={0.5}
          max={2.0}
          step={0.1}
          value={speed}
          onChange={(e) => onSpeedChange(Number(e.target.value))}
          className="w-full accent-indigo-500"
        />
      </div>
      <div className="w-24">
        <label className="mb-1 block text-xs text-slate-500">
          {vt.outputFormat ?? "输出格式"}
        </label>
        <select
          value={format}
          onChange={(e) => onFormatChange(e.target.value as AudioFormat)}
          className="w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-xs focus:ring-2 focus:ring-indigo-500/40 focus:outline-none"
        >
          <option value="mp3">MP3</option>
          <option value="wav">WAV</option>
          <option value="ogg">OGG</option>
        </select>
      </div>
    </div>
  );
}
