"use client";

import { Loader2, X } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import {
  useExtractDocText,
  useSubmitScriptToAudio,
} from "@/core/voice-twin/production-hooks";
import type { DocExtractResult } from "@/core/voice-twin/production-types";
import type { ProductionTask } from "@/core/voice-twin/production-types";
import { cn } from "@/lib/utils";

import type { AudioFormat } from "./speed-format-controls";
import { SpeedFormatControls } from "./speed-format-controls";
import { VoiceSelector } from "./voice-selector";

type Step = "upload" | "preview" | "edit";

interface DocToAudioProps {
  onTaskCreated?: (task: ProductionTask) => void;
  voiceId: string;
  onVoiceIdChange: (voiceId: string) => void;
  speed: number;
  onSpeedChange: (speed: number) => void;
  format: AudioFormat;
  onFormatChange: (format: AudioFormat) => void;
}

const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
const PREVIEW_CHARS = 500;

export function DocToAudio({
  onTaskCreated,
  voiceId,
  onVoiceIdChange,
  speed,
  onSpeedChange,
  format,
  onFormatChange,
}: DocToAudioProps) {
  const { t } = useI18n();
  const vt = t.voiceTwin;
  const { glassStyleNoClip } = useGlassStyle();

  const [step, setStep] = useState<Step>("upload");
  const [extractResult, setExtractResult] = useState<DocExtractResult | null>(
    null,
  );
  const [editedText, setEditedText] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const extract = useExtractDocText();
  const submit = useSubmitScriptToAudio();

  // ── File handling ────────────────────────────────────────────────────

  const extractFailedMsg = vt.submitFailed ?? "提交失败";

  const handleFile = useCallback(
    async (file: File) => {
      setFileError(null);
      if (file.size > MAX_FILE_SIZE) {
        setFileError(`File too large (max ${MAX_FILE_SIZE / 1024 / 1024}MB)`);
        return;
      }
      try {
        const result = await extract.mutateAsync(file);
        setExtractResult(result);
        setEditedText(result.text);
        setStep("preview");
      } catch (e) {
        toast.error(e instanceof Error ? e.message : extractFailedMsg);
      }
    },
    [extract, extractFailedMsg],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) void handleFile(file);
    },
    [handleFile],
  );

  const reset = useCallback(() => {
    setStep("upload");
    setExtractResult(null);
    setEditedText("");
    setFileError(null);
    extract.reset();
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [extract]);

  // ── Submit ───────────────────────────────────────────────────────────

  const submittedMsg = vt.submittedGenerating ?? "已提交，正在生成…";
  const submitFailedMsg = vt.submitFailed ?? "提交失败";

  const handleSubmit = useCallback(
    async (text: string) => {
      if (!voiceId || !text.trim()) return;
      try {
        const task = await submit.mutateAsync({
          voice_id: voiceId,
          text,
          speed_ratio: speed,
          output_format: format,
          title: extractResult?.filename?.replace(/\.[^.]+$/, "") ?? undefined,
        });
        onTaskCreated?.(task);
        reset();
        toast.success(submittedMsg);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : submitFailedMsg);
      }
    },
    [
      voiceId,
      speed,
      format,
      submit,
      onTaskCreated,
      reset,
      extractResult,
      submittedMsg,
      submitFailedMsg,
    ],
  );

  // ── Estimated time ───────────────────────────────────────────────────

  const estimateMin = useCallback(
    (charCount: number) => Math.ceil(charCount / (250 * speed)),
    [speed],
  );

  // ── Render ───────────────────────────────────────────────────────────

  return (
    <div className="rounded-xl p-4" style={glassStyleNoClip()}>
      <h3 className="mb-3 text-sm font-bold">
        {vt.docToAudio ?? "文档转音频"}
      </h3>

      {/* Voice selector — always visible after upload */}
      {(step === "preview" || step === "edit") && (
        <>
          <label className="mb-1 block text-xs text-slate-500">
            {vt.selectVoice ?? "选择音色"}
          </label>
          <VoiceSelector
            value={voiceId}
            onChange={onVoiceIdChange}
            previewText={editedText}
            className="mb-3 w-full"
          />
        </>
      )}

      {/* ── Step 1: Upload ──────────────────────────────────────────── */}
      {step === "upload" && (
        <>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={cn(
              "mb-3 flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-6 transition-colors",
              dragOver
                ? "border-indigo-400 bg-indigo-500/10"
                : "border-white/10 hover:border-white/20",
            )}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".docx,.txt,.md"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void handleFile(file);
              }}
            />
            {extract.isPending ? (
              <>
                <Loader2 className="h-5 w-5 animate-spin text-indigo-400" />
                <p className="text-xs text-slate-400">
                  {vt.docExtracting ?? "正在提取文本..."}
                </p>
              </>
            ) : (
              <>
                <p className="text-xs text-slate-400">
                  {vt.docDropHint ?? "拖拽文档到此处或点击选择"}
                </p>
                <p className="text-xs text-slate-500">
                  {vt.docFileHint ?? ".docx .txt .md（最大 50MB）"}
                </p>
                <p className="text-xs text-slate-500">
                  旧版 .doc 文件请先用 Word/WPS 另存为 .docx
                </p>
              </>
            )}
          </div>

          {(fileError ?? extract.error) && (
            <p className="mb-2 text-xs text-red-500">
              {fileError ??
                (extract.error instanceof Error
                  ? extract.error.message
                  : String(extract.error))}
            </p>
          )}
        </>
      )}

      {/* ── Step 2: Preview ─────────────────────────────────────────── */}
      {step === "preview" && extractResult && (
        <>
          {/* File info */}
          <div className="mb-3 rounded-lg border border-white/5 bg-white/5 px-3 py-2">
            <div className="flex items-center gap-2">
              <span className="text-sm">📄</span>
              <span className="flex-1 truncate text-xs text-slate-300">
                {extractResult.filename}
              </span>
              <button
                type="button"
                onClick={reset}
                className="text-slate-400 hover:text-red-400"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            <div className="mt-1 flex items-center gap-3 text-xs text-slate-500">
              <span>
                {extractResult.char_count.toLocaleString()}{" "}
                {vt.docCharCount ?? "字"}
              </span>
              <span>
                {vt.docEstimatedTime?.replace(
                  "{n}",
                  String(estimateMin(extractResult.char_count)),
                ) ?? `预计 ${estimateMin(extractResult.char_count)} 分钟`}
              </span>
            </div>
            {extractResult.truncated && (
              <p className="mt-1 text-xs text-amber-500">
                {vt.docTruncated ?? "文档过长，已截断前 100,000 字符"}
              </p>
            )}
          </div>

          {/* Text preview */}
          <div className="mb-3 max-h-32 overflow-y-auto rounded-lg border border-white/5 bg-white/5 px-3 py-2">
            <pre className="text-xs whitespace-pre-wrap text-slate-400">
              {extractResult.text.slice(0, PREVIEW_CHARS)}
              {extractResult.text.length > PREVIEW_CHARS && (
                <span className="text-slate-600">
                  ...（共 {extractResult.char_count.toLocaleString()} 字）
                </span>
              )}
            </pre>
          </div>

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

          {/* Actions */}
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => setStep("edit")}
              className="w-full rounded-lg border border-white/10 px-4 py-2 text-xs font-medium text-slate-300 transition-colors hover:bg-white/5"
            >
              {vt.docPreview ?? "编辑文本"}
            </button>
            <button
              type="button"
              onClick={() => handleSubmit(extractResult.text)}
              disabled={!voiceId || submit.isPending}
              className={cn(
                "w-full rounded-lg px-4 py-2 text-sm font-medium text-white transition-all",
                "bg-gradient-to-r from-indigo-500 to-purple-500",
                "hover:from-indigo-600 hover:to-purple-600",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              {submit.isPending
                ? (vt.generating ?? "生成中...")
                : (vt.docDirectGenerate ?? "直接生成")}
            </button>
          </div>
        </>
      )}

      {/* ── Step 3: Edit ────────────────────────────────────────────── */}
      {step === "edit" && (
        <>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs text-slate-500">
              {editedText.length.toLocaleString()} / 100,000{" "}
              {vt.docCharCount ?? "字"}
            </span>
            <span className="text-xs text-slate-500">
              {vt.docEstimatedTime?.replace(
                "{n}",
                String(estimateMin(editedText.length)),
              ) ?? `预计 ${estimateMin(editedText.length)} 分钟`}
            </span>
          </div>
          <textarea
            rows={10}
            maxLength={100000}
            value={editedText}
            onChange={(e) => setEditedText(e.target.value)}
            className="mb-3 w-full resize-none rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs leading-relaxed placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-500/40 focus:outline-none"
          />

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

          {/* Actions */}
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => handleSubmit(editedText)}
              disabled={!voiceId || !editedText.trim() || submit.isPending}
              className={cn(
                "w-full rounded-lg px-4 py-2 text-sm font-medium text-white transition-all",
                "bg-gradient-to-r from-indigo-500 to-purple-500",
                "hover:from-indigo-600 hover:to-purple-600",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              {submit.isPending
                ? (vt.generating ?? "生成中...")
                : (vt.generateAudio ?? "生成音频")}
            </button>
            <button
              type="button"
              onClick={() => setStep("preview")}
              className="w-full rounded-lg border border-white/10 px-4 py-2 text-xs font-medium text-slate-300 transition-colors hover:bg-white/5"
            >
              ← {vt.docPreview ?? "返回预览"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
