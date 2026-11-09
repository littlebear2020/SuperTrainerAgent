"use client";

import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import { useSubmitScriptToAudio } from "@/core/voice-twin/production-hooks";
import type { ProductionTask } from "@/core/voice-twin/production-types";
import { splitTextToSegments } from "@/core/voice-twin/text-split";
import { cn } from "@/lib/utils";

import { SegmentCard } from "./segment-card";
import type { SegmentData } from "./segment-card";
import type { AudioFormat } from "./speed-format-controls";
import { SpeedFormatControls } from "./speed-format-controls";
import { VoiceSelector } from "./voice-selector";

interface ScriptToAudioProps {
  onTaskCreated?: (task: ProductionTask) => void;
  voiceId: string;
  onVoiceIdChange: (voiceId: string) => void;
  speed: number;
  onSpeedChange: (speed: number) => void;
  format: AudioFormat;
  onFormatChange: (format: AudioFormat) => void;
}

export function ScriptToAudio({
  onTaskCreated,
  voiceId,
  onVoiceIdChange,
  speed,
  onSpeedChange,
  format,
  onFormatChange,
}: ScriptToAudioProps) {
  const { t } = useI18n();
  const vt = t.voiceTwin;
  const { glassStyleNoClip } = useGlassStyle();

  const [text, setText] = useState("");
  const [segmentMode, setSegmentMode] = useState(false);
  const [segments, setSegments] = useState<SegmentData[]>([]);

  const submit = useSubmitScriptToAudio();

  // Build segments from text when mode is toggled on
  const enableSegmentMode = useCallback(() => {
    if (!text.trim()) return;
    const parts = splitTextToSegments(text);
    setSegments(
      parts.map((s, i) => ({
        id: `seg-${i}-${Date.now()}`,
        text: s,
        originalText: s,
      })),
    );
    setSegmentMode(true);
  }, [text]);

  const disableSegmentMode = useCallback(() => {
    // Merge segments back into text, preserving paragraph breaks between
    // segments (was join("") which collapsed all separators).
    setText(segments.map((s) => s.text).join("\n\n"));
    setSegmentMode(false);
    setSegments([]);
  }, [segments]);

  const handleSegmentChange = useCallback((id: string, newText: string) => {
    setSegments((prev) =>
      prev.map((s) => (s.id === id ? { ...s, text: newText } : s)),
    );
  }, []);

  const handleSegmentRemove = useCallback((id: string) => {
    setSegments((prev) => prev.filter((s) => s.id !== id));
  }, []);

  // Merged text from all segments (preserve paragraph breaks between segments)
  const mergedText = useMemo(
    () => (segmentMode ? segments.map((s) => s.text).join("\n\n") : text),
    [segmentMode, segments, text],
  );

  // Estimated runtime in minutes (~250 chars/min, scaled by speed). Mirrors
  // doc-to-audio so the two tabs have aligned feedback.
  const estimateMin = (charCount: number) =>
    Math.max(1, Math.ceil(charCount / (250 * speed)));

  const submittedMsg = vt.submittedGenerating ?? "已提交，正在生成…";
  const failedMsg = vt.submitFailed ?? "提交失败";

  const handleSubmit = useCallback(async () => {
    if (!voiceId || !mergedText.trim()) return;
    try {
      const task = await submit.mutateAsync({
        voice_id: voiceId,
        text: mergedText,
        speed_ratio: speed,
        output_format: format,
      });
      onTaskCreated?.(task);
      setText("");
      setSegments([]);
      setSegmentMode(false);
      toast.success(submittedMsg);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : failedMsg);
    }
  }, [
    voiceId,
    mergedText,
    speed,
    format,
    submit,
    onTaskCreated,
    submittedMsg,
    failedMsg,
  ]);

  const segmentCount = segments.length;

  return (
    <div className="rounded-xl p-4" style={glassStyleNoClip()}>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-bold">
          {vt.scriptToAudio ?? "文稿转音频"}
        </h3>

        {/* Smart segmentation toggle */}
        <button
          type="button"
          onClick={segmentMode ? disableSegmentMode : enableSegmentMode}
          disabled={!text.trim() && !segmentMode}
          className={cn(
            "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
            segmentMode
              ? "bg-indigo-500/20 text-indigo-500"
              : "bg-white/5 text-slate-400 hover:bg-white/10 hover:text-slate-300",
            "disabled:cursor-not-allowed disabled:opacity-40",
          )}
        >
          {vt.smartSegmentation ?? "智能分段"}
          {segmentMode && segmentCount > 0 ? ` (${segmentCount})` : ""}
        </button>
      </div>

      {/* Voice selector */}
      <label className="mb-1 block text-xs text-slate-500">
        {vt.selectVoice ?? "选择音色"}
      </label>
      <VoiceSelector
        value={voiceId}
        onChange={onVoiceIdChange}
        previewText={text}
        className="mb-3 w-full"
      />

      {/* Char count + estimated runtime (aligned with doc-to-audio) */}
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs text-slate-500">
          {mergedText.length.toLocaleString()} / 100,000{" "}
          {vt.docCharCount ?? "字"}
        </span>
        {mergedText.trim() && (
          <span className="text-xs text-slate-500">
            {vt.docEstimatedTime?.replace(
              "{n}",
              String(estimateMin(mergedText.length)),
            ) ?? `预计 ${estimateMin(mergedText.length)} 分钟`}
          </span>
        )}
      </div>

      {/* Text input or segment editor */}
      {!segmentMode ? (
        <textarea
          rows={6}
          maxLength={100000}
          placeholder={vt.scriptPlaceholder ?? "粘贴或输入讲解稿文本..."}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="mb-3 w-full resize-none rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-500/40 focus:outline-none"
        />
      ) : (
        <div className="mb-3 flex max-h-80 flex-col gap-2 overflow-y-auto">
          {segments.map((seg, i) => (
            <SegmentCard
              key={seg.id}
              index={i}
              segment={seg}
              onChange={handleSegmentChange}
              onRemove={handleSegmentRemove}
            />
          ))}
          {segments.length === 0 && (
            <p className="py-4 text-center text-xs text-slate-400">
              无分段内容
            </p>
          )}
        </div>
      )}

      {/* Speed + format (shared component) */}
      <SpeedFormatControls
        speed={speed}
        format={format}
        onSpeedChange={onSpeedChange}
        onFormatChange={onFormatChange}
      />

      {/* Error */}
      {submit.error && (
        <p className="mb-2 text-xs text-red-500">
          {submit.error instanceof Error
            ? submit.error.message
            : String(submit.error)}
        </p>
      )}

      {/* Submit */}
      <button
        type="button"
        onClick={handleSubmit}
        disabled={!voiceId || !mergedText.trim() || submit.isPending}
        className={cn(
          "w-full rounded-lg px-4 py-2 text-sm font-medium text-white transition-all",
          "bg-gradient-to-r from-indigo-500 to-purple-500",
          "hover:from-indigo-600 hover:to-purple-600",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
      >
        {submit.isPending
          ? (vt.generating ?? "生成中...")
          : segmentMode
            ? (vt.mergeAndGenerate ?? "合并生成")
            : (vt.generateAudio ?? "生成音频")}
      </button>
    </div>
  );
}
