"use client";

import { useCallback, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import { useSynthesize } from "@/core/voice-twin/hooks";
import type { SynthOutputFormat } from "@/core/voice-twin/types";
import { cn } from "@/lib/utils";

import { AudioPlayer } from "./audio-player";

interface SynthesizeDialogProps {
  voiceId: string;
  voiceName: string;
  open: boolean;
  onClose: () => void;
}

export function SynthesizeDialog({
  voiceId,
  voiceName,
  open,
  onClose,
}: SynthesizeDialogProps) {
  const { t } = useI18n();
  const vt = t.voiceTwin;
  const { glassStyle } = useGlassStyle();

  const [text, setText] = useState("");
  const [speed, setSpeed] = useState(1.0);
  const [format, setFormat] = useState<SynthOutputFormat>("mp3");
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  const synthesize = useSynthesize();

  const handleClose = useCallback(() => {
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
    }
    setText("");
    setSpeed(1.0);
    setFormat("mp3");
    setAudioUrl(null);
    synthesize.reset();
    onClose();
  }, [audioUrl, onClose, synthesize]);

  const handleSubmit = useCallback(async () => {
    if (!text.trim()) return;
    try {
      const url = await synthesize.mutateAsync({
        voiceId,
        params: {
          text,
          output_format: format,
          speed_ratio: speed,
        },
      });
      setAudioUrl(url);
    } catch {
      // error is available via synthesize.error
    }
  }, [text, voiceId, format, speed, synthesize]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={handleClose}
    >
      <div
        className="mx-4 w-full max-w-md rounded-2xl p-6"
        style={glassStyle()}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Title */}
        <h2 className="mb-4 text-lg font-semibold">
          {vt.synthesizeTitle} — {voiceName}
        </h2>

        {/* Textarea */}
        <textarea
          rows={4}
          maxLength={5000}
          placeholder={vt.synthesizePlaceholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="mb-4 w-full resize-none rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-500/40 focus:outline-none"
        />

        {/* Speed slider */}
        <div className="mb-4">
          <label className="mb-1 block text-xs text-slate-500">
            {vt.speedRatio}: {speed.toFixed(1)}
          </label>
          <input
            type="range"
            min={0.5}
            max={2.0}
            step={0.1}
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
            className="w-full accent-indigo-500"
          />
        </div>

        {/* Format select */}
        <div className="mb-4">
          <label className="mb-1 block text-xs text-slate-500">
            {vt.outputFormat}
          </label>
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value as SynthOutputFormat)}
            className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500/40 focus:outline-none"
          >
            <option value="mp3">MP3</option>
            <option value="wav">WAV</option>
            <option value="ogg">OGG</option>
          </select>
        </div>

        {/* Audio player */}
        {audioUrl && (
          <div className="mb-4">
            <AudioPlayer src={audioUrl} />
          </div>
        )}

        {/* Error message */}
        {synthesize.error && (
          <p className="mb-3 text-xs text-red-500">
            {synthesize.error instanceof Error
              ? synthesize.error.message
              : String(synthesize.error)}
          </p>
        )}

        {/* Buttons */}
        <div className="flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={handleClose}
            className="rounded-lg px-4 py-2 text-sm text-slate-400 transition-colors hover:bg-white/5 hover:text-slate-600"
          >
            {t.common.cancel}
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!text.trim() || synthesize.isPending}
            className={cn(
              "rounded-lg px-4 py-2 text-sm font-medium text-white transition-all",
              "bg-gradient-to-r from-indigo-500 to-purple-500",
              "hover:from-indigo-600 hover:to-purple-600",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {synthesize.isPending ? vt.synthesizing : vt.synthesizeButton}
          </button>
        </div>
      </div>
    </div>
  );
}
