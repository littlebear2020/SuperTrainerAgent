"use client";

import { X } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import { useSubmitPptToVideo } from "@/core/voice-twin/production-hooks";
import type { ProductionTask } from "@/core/voice-twin/production-types";
import { cn } from "@/lib/utils";

import { VoiceSelector } from "./voice-selector";

interface PptToVideoProps {
  onTaskCreated?: (task: ProductionTask) => void;
  voiceId: string;
  onVoiceIdChange: (voiceId: string) => void;
}

const PRESET_OPTIONS = [
  {
    value: "none",
    labelZh: "无动画（硬切）",
    labelEn: "No animation (hard cut)",
  },
  { value: "smooth", labelZh: "平滑", labelEn: "Smooth (Morph)" },
  { value: "fade", labelZh: "淡出", labelEn: "Fade" },
  { value: "cut", labelZh: "切出", labelEn: "Cut" },
  { value: "wipe", labelZh: "擦除", labelEn: "Wipe" },
] as const;

export function PptToVideo({
  onTaskCreated,
  voiceId,
  onVoiceIdChange,
}: PptToVideoProps) {
  const { t } = useI18n();
  const vt = t.voiceTwin;
  const { glassStyleNoClip } = useGlassStyle();

  const [preset, setPreset] = useState("none");
  const [subtitle, setSubtitle] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const submit = useSubmitPptToVideo();

  const addFiles = useCallback((incoming: FileList | File[]) => {
    // Only .pptx — the backend (python-pptx) cannot parse legacy .ppt, so
    // accepting it here just delayed the failure to processing time.
    const valid = Array.from(incoming).filter((f) => /\.pptx$/i.test(f.name));
    setFiles((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [...prev, ...valid.filter((f) => !names.has(f.name))];
    });
  }, []);

  const removeFile = useCallback((name: string) => {
    setFiles((prev) => prev.filter((f) => f.name !== name));
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      addFiles(e.dataTransfer.files);
    },
    [addFiles],
  );

  const submittedMsg = vt.submittedGenerating ?? "已提交，正在生成…";
  const submitFailedMsg = vt.submitFailed ?? "提交失败";

  const handleSubmit = useCallback(async () => {
    if (!voiceId || files.length === 0) return;
    setSubmitting(true);
    try {
      for (const file of files) {
        const task = await submit.mutateAsync({
          file,
          voice_id: voiceId,
          preset,
          subtitle: subtitle ? "true" : "false",
        });
        onTaskCreated?.(task);
        // Drop each file as soon as ITS submit succeeds, so a retry after a
        // mid-batch failure only resubmits the files that actually failed.
        setFiles((prev) => prev.filter((f) => f !== file));
      }
      setFiles([]);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      toast.success(submittedMsg);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : submitFailedMsg);
    } finally {
      setSubmitting(false);
    }
  }, [
    voiceId,
    files,
    preset,
    subtitle,
    submit,
    onTaskCreated,
    submittedMsg,
    submitFailedMsg,
  ]);

  return (
    <div className="rounded-xl p-4" style={glassStyleNoClip()}>
      <h3 className="mb-3 text-sm font-bold">
        {vt.pptToVideo ?? "PPT 转视频"}
      </h3>

      {/* Voice selector */}
      <label className="mb-1 block text-xs text-slate-500">
        {vt.selectVoice ?? "选择音色"}
      </label>
      <VoiceSelector
        value={voiceId}
        onChange={onVoiceIdChange}
        className="mb-3 w-full"
      />

      {/* File drop zone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className={cn(
          "mb-2 flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-4 transition-colors",
          dragOver
            ? "border-indigo-400 bg-indigo-500/10"
            : "border-white/10 hover:border-white/20",
          files.length > 0 && "border-green-500/40 bg-green-500/5",
        )}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pptx"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
          }}
        />
        <p className="text-xs text-slate-400">
          {vt.pptDropHint ?? "拖拽 PPT 文件到此处或点击选择"}
        </p>
        <p className="text-xs text-slate-500">
          {vt.pptFileHint ?? ".pptx（支持多选，每个最大 100MB）"}
        </p>
        <p className="text-xs text-slate-600">{vt.fontGateHint}</p>
      </div>

      {/* File list */}
      {files.length > 0 && (
        <div className="mb-3 space-y-1">
          {files.map((f) => (
            <div
              key={f.name}
              className="flex items-center gap-2 rounded border border-white/5 px-2 py-1"
            >
              <span className="min-w-0 flex-1 truncate text-xs text-green-400">
                {f.name}
              </span>
              <button
                type="button"
                onClick={() => removeFile(f.name)}
                className="text-slate-400 hover:text-red-400"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Preset selector */}
      <div className="mb-3">
        <label className="mb-1 block text-xs text-slate-500">
          {vt.preset ?? "预设效果"}
        </label>
        <select
          value={preset}
          onChange={(e) => setPreset(e.target.value)}
          className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs focus:ring-2 focus:ring-indigo-500/40 focus:outline-none"
        >
          {PRESET_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.labelZh}
            </option>
          ))}
        </select>
      </div>

      {/* Subtitle toggle */}
      <div className="mb-3">
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={subtitle}
            onChange={(e) => setSubtitle(e.target.checked)}
            className="accent-indigo-500"
          />
          {vt.subtitleOverlay ?? "显示字幕"}
        </label>
        {subtitle && (
          <p className="mt-1 text-xs text-slate-500">
            {vt.subtitleHint ?? "根据文稿内容自动生成字幕"}
          </p>
        )}
      </div>

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
        disabled={!voiceId || files.length === 0 || submitting}
        className={cn(
          "w-full rounded-lg px-4 py-2 text-sm font-medium text-white transition-all",
          "bg-gradient-to-r from-indigo-500 to-purple-500",
          "hover:from-indigo-600 hover:to-purple-600",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
      >
        {submitting
          ? (vt.generating ?? "生成中...")
          : files.length > 1
            ? `${vt.generateVideo ?? "生成视频"} (${files.length} 个文件)`
            : (vt.generateVideo ?? "生成视频")}
      </button>
    </div>
  );
}
