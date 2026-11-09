"use client";

import { ArrowLeft, Mic, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import {
  useVoiceTwinModels,
  usePrescreenAudio,
  useUploadVoiceTwin,
} from "@/core/voice-twin/hooks";
import { localPrescreen } from "@/core/voice-twin/prescreen";
import type { PrescreenResponse } from "@/core/voice-twin/types";
import { cn } from "@/lib/utils";

import { Teleprompter } from "./teleprompter";
import { DEFAULT_TELEPROMPTER_TEXTS } from "./teleprompter";
import { isRecordingSupported, VoiceRecorder } from "./voice-recorder";
import { WaveformPreview } from "./waveform-preview";

// ── Types ──────────────────────────────────────────────────────────────────

type PanelMode = "upload" | "recording";

// ── Constants ──────────────────────────────────────────────────────────────

const MAX_FILE_BYTES = 10 * 1024 * 1024; // 10MB (Volcengine API limit)
const MAX_RECORDING_SEC = 180; // 3 min → ~8.5MB WAV, safe under 10MB

/** Default clone model when the models list is unavailable (ICL 3.0). */
const DEFAULT_MODEL_TYPE = 5;

/**
 * Training-audio language options — values mirror the backend language codes
 * (volcengine_client._LANGUAGE_MAP): 0 中文 / 1 en / 2 ja / 3 es / 4 id /
 * 5 pt / 6 de / 7 fr.
 */
const LANGUAGE_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: "中文" },
  { value: 1, label: "English" },
  { value: 2, label: "日本語" },
  { value: 3, label: "Español" },
  { value: 4, label: "Bahasa Indonesia" },
  { value: 5, label: "Português" },
  { value: 6, label: "Deutsch" },
  { value: 7, label: "Français" },
];

// ── Component ──────────────────────────────────────────────────────────────

export function UploadPanel() {
  const { t } = useI18n();
  const vt = t.voiceTwin;
  const { glassStyle, isDark } = useGlassStyle();
  const prescreen = usePrescreenAudio();
  const upload = useUploadVoiceTwin();
  const modelsQuery = useVoiceTwinModels();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const [voiceName, setVoiceName] = useState("");
  const [speakerId, setSpeakerId] = useState("");
  const [modelType, setModelType] = useState(DEFAULT_MODEL_TYPE);
  const [language, setLanguage] = useState(0);
  const [fileSizeError, setFileSizeError] = useState(false);
  const [prescreenResult, setPrescreenResult] = useState<{
    temp_file_id: string;
    duration: number;
    format: string;
  } | null>(null);
  const [audioPreviewUrl, setAudioPreviewUrl] = useState<string | null>(null);

  // ── Recording state ──────────────────────────────────────────────────────
  const [mode, setMode] = useState<PanelMode>("upload");
  const [localErrors, setLocalErrors] = useState<string[]>([]);
  const [localWarnings, setLocalWarnings] = useState<string[]>([]);

  const [recordingSupported, setRecordingSupported] = useState(false);

  // Defer browser-API check to after hydration to avoid SSR/client mismatch
  useEffect(() => {
    setRecordingSupported(isRecordingSupported());
  }, []);

  // Teleprompter state
  const [teleprompterText, setTeleprompterText] = useState(
    DEFAULT_TELEPROMPTER_TEXTS.zh,
  );
  const [teleprompterSpeed, setTeleprompterSpeed] = useState(1.0);
  const [isRecorderActive, setIsRecorderActive] = useState(false);

  // ── Helpers ──────────────────────────────────────────────────────────────

  // Toast messages (hoisted so useCallback deps stay stable & lint-clean).
  const submittedMsg = vt.submittedGenerating ?? "已提交，正在训练…";
  const submitFailedMsg = vt.submitFailed ?? "提交失败";
  const prescreenFailedMsg = vt.prescreenFailed ?? "预检失败";

  const resetToUpload = useCallback(() => {
    if (audioPreviewUrl) URL.revokeObjectURL(audioPreviewUrl);
    setMode("upload");
    setLocalErrors([]);
    setLocalWarnings([]);
    setFileSizeError(false);
    setIsRecorderActive(false);
    setTeleprompterText(DEFAULT_TELEPROMPTER_TEXTS.zh);
    setTeleprompterSpeed(1.0);
    setAudioPreviewUrl(null);
    // Clear prescreen so the form returns to the initial upload/record phase.
    setPrescreenResult(null);
  }, [audioPreviewUrl]);

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleFile = useCallback(
    async (file: File) => {
      setFileSizeError(false);
      if (audioPreviewUrl) URL.revokeObjectURL(audioPreviewUrl);
      if (file.size > MAX_FILE_BYTES) {
        setFileSizeError(true);
        return;
      }
      try {
        // Compute duration locally first (instant, via Web Audio API) so the
        // user sees the real value immediately — not the legacy 0.0s placeholder.
        const local = await localPrescreen(file);
        const result: PrescreenResponse = await prescreen.mutateAsync(file);
        if (result.status === "pass") {
          setAudioPreviewUrl(URL.createObjectURL(file));
          setPrescreenResult({
            temp_file_id: result.temp_file_id,
            // Prefer the locally-decoded duration (immediate); fall back to the
            // server value if local decode failed.
            duration: local.duration > 0 ? local.duration : result.duration,
            format: result.format,
          });
        }
      } catch (e) {
        // Error is also captured in prescreen.error for inline display.
        toast.error(e instanceof Error ? e.message : prescreenFailedMsg);
      }
    },
    [prescreen, audioPreviewUrl, prescreenFailedMsg],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) {
        void handleFile(file);
      }
    },
    [handleFile],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        void handleFile(file);
      }
    },
    [handleFile],
  );

  const handleRecordingComplete = useCallback(
    async (file: File, _duration: number) => {
      // Run local prescreen first
      const local = await localPrescreen(file);
      if (!local.valid) {
        setLocalErrors(local.errors);
        setLocalWarnings(local.warnings);
        return;
      }
      // Clear local errors, then run server prescreen
      setLocalErrors([]);
      setLocalWarnings(local.warnings);
      try {
        const result: PrescreenResponse = await prescreen.mutateAsync(file);
        if (result.status === "pass") {
          setAudioPreviewUrl(URL.createObjectURL(file));
          setPrescreenResult({
            temp_file_id: result.temp_file_id,
            // Prefer the locally-decoded duration (already computed above).
            duration: local.duration > 0 ? local.duration : result.duration,
            format: result.format,
          });
          setMode("upload");
        }
      } catch (e) {
        // prescreen.error will be set via mutation error; also surface as toast.
        toast.error(e instanceof Error ? e.message : prescreenFailedMsg);
      }
    },
    [prescreen, prescreenFailedMsg],
  );

  const handleSubmit = useCallback(() => {
    if (!prescreenResult || !voiceName.trim() || !speakerId.trim()) return;

    upload.mutate(
      {
        temp_file_id: prescreenResult.temp_file_id,
        voice_name: voiceName.trim(),
        model_type: modelType,
        language,
        speaker_id: speakerId.trim(),
      },
      {
        onSuccess: () => {
          if (audioPreviewUrl) URL.revokeObjectURL(audioPreviewUrl);
          setPrescreenResult(null);
          setAudioPreviewUrl(null);
          setVoiceName("");
          setSpeakerId("");
          setFileSizeError(false);
          if (fileInputRef.current) {
            fileInputRef.current.value = "";
          }
          toast.success(submittedMsg);
        },
        onError: (err) => {
          toast.error(err instanceof Error ? err.message : submitFailedMsg);
        },
      },
    );
  }, [
    prescreenResult,
    voiceName,
    speakerId,
    modelType,
    language,
    upload,
    audioPreviewUrl,
    submittedMsg,
    submitFailedMsg,
  ]);

  // Cleanup blob URL on unmount
  useEffect(() => {
    return () => {
      if (audioPreviewUrl) URL.revokeObjectURL(audioPreviewUrl);
    };
  }, [audioPreviewUrl]);

  // ── Determine phase ──────────────────────────────────────────────────────

  const prescreenFailed = prescreen.isError && !prescreenResult;

  // Model list: when the query fails or returns nothing, the selector is
  // simply not rendered and submission falls back to DEFAULT_MODEL_TYPE.
  const models = modelsQuery.data ?? [];
  const selectedModel = models.find((m) => m.model_type === modelType);
  const languageUnsupported =
    selectedModel !== undefined && !selectedModel.languages.includes(language);

  // ── Render ───────────────────────────────────────────────────────────────

  // ── Recording mode ───────────────────────────────────────────────────────
  if (mode === "recording") {
    return (
      <div
        className="relative flex flex-col gap-4 rounded-2xl p-6"
        style={glassStyle()}
      >
        {/* Back button */}
        <button
          type="button"
          onClick={resetToUpload}
          className={cn(
            "inline-flex items-center gap-1.5 self-start rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
            "bg-slate-500/10 text-slate-500 hover:bg-slate-500/20",
          )}
        >
          <ArrowLeft className="h-3 w-3" />
          {vt.backToUpload}
        </button>

        {/* Local prescreen errors */}
        {localErrors.length > 0 && (
          <div className="flex flex-col gap-1 rounded-lg bg-red-500/10 px-3 py-2">
            <span className="text-xs font-medium text-red-500">
              {vt.localPrescreenFailed}
            </span>
            {localErrors.map((err, i) => (
              <span key={i} className="text-xs text-red-400">
                {err}
              </span>
            ))}
          </div>
        )}

        {/* Local prescreen warnings */}
        {localWarnings.length > 0 && (
          <div className="flex flex-col gap-1 rounded-lg bg-yellow-500/10 px-3 py-2">
            {localWarnings.map((warn, i) => (
              <span key={i} className="text-xs text-yellow-500">
                {warn}
              </span>
            ))}
          </div>
        )}

        {/* Teleprompter text selector */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">
              {vt.teleprompterText}
            </span>
            <select
              value={
                teleprompterText === DEFAULT_TELEPROMPTER_TEXTS.zh
                  ? "zh"
                  : teleprompterText === DEFAULT_TELEPROMPTER_TEXTS.en
                    ? "en"
                    : "custom"
              }
              onChange={(e) => {
                if (e.target.value === "zh")
                  setTeleprompterText(DEFAULT_TELEPROMPTER_TEXTS.zh);
                else if (e.target.value === "en")
                  setTeleprompterText(DEFAULT_TELEPROMPTER_TEXTS.en);
                else setTeleprompterText("");
              }}
              className={cn(
                "rounded border px-2 py-0.5 text-xs outline-none",
                isDark
                  ? "border-white/10 bg-white/5 text-white"
                  : "border-black/10 bg-black/5 text-slate-900",
              )}
            >
              <option value="zh">中文</option>
              <option value="en">English</option>
              <option value="custom">{vt.customText}</option>
            </select>
          </div>
          {teleprompterText === "" && (
            <textarea
              value={teleprompterText}
              onChange={(e) => setTeleprompterText(e.target.value)}
              placeholder={vt.customText}
              rows={3}
              className={cn(
                "w-full rounded-lg border px-3 py-2 text-xs outline-none",
                isDark
                  ? "border-white/10 bg-white/5 text-white placeholder:text-slate-500"
                  : "border-black/10 bg-black/5 text-slate-900 placeholder:text-slate-400",
              )}
            />
          )}
        </div>

        {/* Teleprompter */}
        <Teleprompter
          text={teleprompterText ?? DEFAULT_TELEPROMPTER_TEXTS.zh ?? ""}
          speed={teleprompterSpeed}
          isRecording={isRecorderActive}
        />

        {/* VoiceRecorder */}
        <VoiceRecorder
          onComplete={handleRecordingComplete}
          // 31s (not 30s): the recorder counts integer seconds, but prescreen
          // validates against the real decoded duration, which can decode to
          // ~29.8s for an exact 30s take and falsely reject it. One second of
          // headroom keeps the two thresholds from fighting.
          minimumDuration={31}
          maximumDuration={MAX_RECORDING_SEC}
        />

        {/* Hint */}
        <p className="text-center text-xs text-slate-400">
          {vt.minimumDurationHint}
        </p>

        {/* Server prescreen error (from recording flow) */}
        {prescreenFailed && (
          <div className="flex flex-col items-center">
            <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-3 py-1 text-xs font-medium text-red-500">
              <Mic className="h-3 w-3" />
              {vt.prescreenFailed}:{" "}
              {prescreen.error?.message ?? "Unknown error"}
            </span>
          </div>
        )}

        {/* Prescreen loading indicator */}
        {prescreen.isPending && (
          <div className="flex items-center justify-center gap-2 text-xs text-slate-400">
            <svg
              className="h-3 w-3 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
              />
            </svg>
            {vt.upload}…
          </div>
        )}
      </div>
    );
  }

  // ── Upload mode (default + form) ─────────────────────────────────────────
  return (
    <div
      className={cn(
        "relative rounded-2xl p-6 transition-colors",
        dragOver && "ring-2 ring-indigo-400/60",
      )}
      style={glassStyle()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      {/* Top: icon + hint (always visible) */}
      <div className="flex flex-col items-center gap-3">
        <div
          className={cn(
            "flex h-20 w-20 items-center justify-center rounded-full",
            isDark ? "bg-white/5" : "bg-black/5",
          )}
        >
          <Mic className="h-12 w-12 text-indigo-400" />
        </div>
        <p className="text-sm font-medium">{vt.uploadHint}</p>
        <p className="text-xs text-slate-400">{vt.supportedFormats}</p>
      </div>

      {/* File size error */}
      {fileSizeError && (
        <div className="mt-4 flex flex-col items-center">
          <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-3 py-1 text-xs font-medium text-red-500">
            {vt.fileTooLarge ?? "文件过大，最大支持 10MB"}
          </span>
        </div>
      )}

      {/* Phase 2: Prescreen passed */}
      {prescreenResult && (
        <div className="mt-5 flex flex-col gap-3">
          {/* Prescreen pass info */}
          <div className="flex items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-500">
              <Mic className="h-3 w-3" />
              {vt.prescreenPass} &mdash; {vt.durationLabel}:{" "}
              {prescreenResult.duration.toFixed(1)}s
            </span>
            {/* Always-available re-select so the user can swap the audio without
                refreshing (the upload/record buttons only show in the initial
                empty phase). */}
            <button
              type="button"
              onClick={resetToUpload}
              className={cn(
                "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
                "text-slate-400 hover:bg-slate-500/10 hover:text-slate-300",
              )}
            >
              <ArrowLeft className="h-3 w-3" />
              {vt.changeAudio ?? "换一个"}
            </button>
          </div>

          {/* Waveform preview */}
          {audioPreviewUrl && <WaveformPreview audioUrl={audioPreviewUrl} />}

          {/* Voice name input */}
          <input
            type="text"
            value={voiceName}
            onChange={(e) => setVoiceName(e.target.value)}
            placeholder={vt.voiceNamePlaceholder}
            className={cn(
              "w-full rounded-lg border px-3 py-2 text-sm transition-colors outline-none",
              isDark
                ? "border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus:border-indigo-400/60"
                : "border-black/10 bg-black/5 text-slate-900 placeholder:text-slate-400 focus:border-indigo-500/60",
            )}
          />

          {/* Speaker ID (pre-paid, required) */}
          <div className="flex flex-col gap-1">
            <input
              type="text"
              value={speakerId}
              onChange={(e) => setSpeakerId(e.target.value)}
              placeholder={vt.speakerIdPlaceholder}
              className={cn(
                "w-full rounded-lg border px-3 py-2 text-sm transition-colors outline-none",
                isDark
                  ? "border-white/10 bg-white/5 text-white placeholder:text-slate-500 focus:border-indigo-400/60"
                  : "border-black/10 bg-black/5 text-slate-900 placeholder:text-slate-400 focus:border-indigo-500/60",
              )}
            />
            <span className="text-xs text-amber-500">
              {vt.speakerIdRequired}
            </span>
          </div>

          {/* Clone model selector (hidden when the models list fails to
              load — submission then falls back to the default model) */}
          {models.length > 0 && (
            <div className="flex flex-col gap-1">
              <span className="text-xs text-slate-400">克隆模型</span>
              <select
                data-testid="model-type-select"
                value={String(modelType)}
                onChange={(e) => setModelType(Number(e.target.value))}
                className={cn(
                  "w-full rounded-lg border px-3 py-2 text-sm transition-colors outline-none",
                  isDark
                    ? "border-white/10 bg-white/5 text-white focus:border-indigo-400/60"
                    : "border-black/10 bg-black/5 text-slate-900 focus:border-indigo-500/60",
                )}
              >
                {models.map((m) => (
                  <option key={m.model_type} value={String(m.model_type)}>
                    {m.name} — {m.description}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Training-audio language */}
          <div className="flex flex-col gap-1">
            <span className="text-xs text-slate-400">训练音频语言</span>
            <select
              data-testid="language-select"
              value={String(language)}
              onChange={(e) => setLanguage(Number(e.target.value))}
              className={cn(
                "w-full rounded-lg border px-3 py-2 text-sm transition-colors outline-none",
                isDark
                  ? "border-white/10 bg-white/5 text-white focus:border-indigo-400/60"
                  : "border-black/10 bg-black/5 text-slate-900 focus:border-indigo-500/60",
              )}
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <option key={opt.value} value={String(opt.value)}>
                  {opt.label}
                </option>
              ))}
            </select>
            {languageUnsupported && (
              <span className="text-xs text-amber-500">
                该模型不支持所选语言
              </span>
            )}
          </div>

          {/* Advanced denoising settings removed: Volcengine defaults are
              sufficient; the previous UI never wired extraParams through to
              the request, so it was a no-op. */}

          {/* Submit button */}
          <button
            type="button"
            onClick={handleSubmit}
            disabled={
              !voiceName.trim() || !speakerId.trim() || upload.isPending
            }
            className={cn(
              "w-full rounded-lg py-2 text-sm font-medium text-white transition-opacity",
              "bg-gradient-to-r from-indigo-500 to-purple-500",
              "hover:opacity-90",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {upload.isPending ? vt.submitting : vt.submitTraining}
          </button>

          {/* Upload error */}
          {upload.isError && (
            <p className="text-xs text-red-400">
              {upload.error?.message ?? "Upload failed"}
            </p>
          )}
        </div>
      )}

      {/* Phase 2: Prescreen failed */}
      {prescreenFailed && (
        <div className="mt-5 flex flex-col items-center">
          <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-3 py-1 text-xs font-medium text-red-500">
            <Mic className="h-3 w-3" />
            {vt.prescreenFailed}: {prescreen.error?.message ?? "Unknown error"}
          </span>
        </div>
      )}

      {/* Phase 1: Default — upload/record buttons */}
      {!prescreenResult && !prescreenFailed && (
        <div className="mt-5 flex justify-center gap-3">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-medium transition-colors",
              "bg-indigo-500/10 text-indigo-500 hover:bg-indigo-500/20",
            )}
          >
            <Upload className="h-3.5 w-3.5" />
            {vt.upload}
          </button>
          {recordingSupported && (
            <button
              type="button"
              onClick={() => setMode("recording")}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-medium transition-colors",
                "bg-indigo-500/10 text-indigo-500 hover:bg-indigo-500/20",
              )}
            >
              <Mic className="h-3.5 w-3.5" />
              {vt.record}
            </button>
          )}
        </div>
      )}

      {/* Prescreen loading indicator */}
      {prescreen.isPending && (
        <div className="mt-3 flex items-center justify-center gap-2 text-xs text-slate-400">
          <svg className="h-3 w-3 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
            />
          </svg>
          {vt.upload}…
        </div>
      )}

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".wav,.mp3,.ogg,.m4a,.aac,.webm"
        onChange={handleInputChange}
        className="hidden"
      />
    </div>
  );
}
