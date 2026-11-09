"use client";

import type { Editor } from "@tiptap/react";
import { Download, Loader2, Mic, Pause, Play, Volume2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  getProductionDownloadURL,
  getProductionStreamURL,
  submitScriptToAudio,
} from "@/core/voice-twin/production-api";
import type { ProductionTask } from "@/core/voice-twin/production-types";
import {
  parseScriptScenes,
  scenesToNarrationText,
  totalEstimatedDuration,
  type ScriptScene,
} from "@/core/writer/script-parser";
import type { DocType } from "@/core/writer/types";
import { cn } from "@/lib/utils";

import { VoiceSelector } from "../voice-twin/voice-selector";

// ── Types ────────────────────────────────────────────────────────────────

type PreviewRange = "full" | "selection" | "scene";

interface VoicePanelProps {
  /** Full document Markdown. */
  markdown: string;
  /**
   * 编辑器实例——选区订阅下沉（2026-10-06 性能批）：修前 writer-page 持有
   * selectedText state + selectionUpdate 监听，拖选/扩选每次都整树重渲；
   * 现由本面板（选区 TTS 的唯一消费方）自行订阅，选区变化只重渲本面板。
   */
  editor: Editor | null;
  /** Document type — enables scene parsing when "script". */
  docType: DocType;
  /** Document title (used as TTS task title). */
  docTitle?: string;
}

// ── Helpers ──────────────────────────────────────────────────────────────

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ── Component ────────────────────────────────────────────────────────────

export function VoicePanel({
  markdown,
  editor,
  docType,
  docTitle,
}: VoicePanelProps) {
  const [selection, setSelection] = useState("");
  useEffect(() => {
    if (!editor) return;
    const updateSelection = () => {
      const { from, to } = editor.state.selection;
      setSelection(
        from !== to ? editor.state.doc.textBetween(from, to, "\n") : "",
      );
    };
    // 挂载即读一次当前选区（评估回馈#1）：面板晚于拖选挂载时（懒挂载），
    // 只靠事件订阅会以空串起步——「选区」按钮误置灰到下一次拖选。
    updateSelection();
    editor.on("selectionUpdate", updateSelection);
    return () => {
      editor.off("selectionUpdate", updateSelection);
    };
  }, [editor]);
  const [voiceId, setVoiceId] = useState("");
  const [range, setRange] = useState<PreviewRange>("full");
  const [sceneIdx, setSceneIdx] = useState(0);
  const [speed, setSpeed] = useState(1.0);

  // Task state
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState<ProductionTask | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Audio playback
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [audioProgress, setAudioProgress] = useState(0);
  const [audioDuration, setAudioDuration] = useState(0);

  // ── Script scene parsing ────────────────────────────────────────────

  // 2026-10-06 性能批：击键 setMarkdown 引发的重渲不再重复解析分镜
  const scenes: ScriptScene[] = useMemo(
    () => (docType === "script" ? parseScriptScenes(markdown) : []),
    [docType, markdown],
  );
  const isScript = scenes.length > 0;
  const totalDuration = totalEstimatedDuration(scenes);

  // ── Determine TTS text based on range ──────────────────────────────

  const ttsText = (() => {
    if (range === "selection" && selection?.trim()) {
      return selection.trim();
    }
    if (range === "scene" && isScript && scenes[sceneIdx]) {
      return scenes[sceneIdx].narration;
    }
    // full text — for scripts, concatenate narrations; otherwise full markdown
    if (isScript) return scenesToNarrationText(scenes) || markdown;
    return markdown;
  })();

  const charCount = ttsText.length;
  const canSubmit = voiceId && ttsText.trim().length > 0 && !submitting;

  // ── Submit TTS task ────────────────────────────────────────────────

  const handleSubmit = useCallback(async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    setTask(null);
    try {
      const result = await submitScriptToAudio({
        voice_id: voiceId,
        text: ttsText,
        speed_ratio: speed,
        output_format: "mp3",
        title: docTitle,
      });
      setTask(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [canSubmit, voiceId, ttsText, speed, docTitle]);

  // ── Poll task status until ready/failed ─────────────────────────────

  useEffect(() => {
    if (!task) return;
    if (task.status === "ready" || task.status === "failed") return;

    let cancelled = false;
    const interval = setInterval(() => {
      void (async () => {
        try {
          const { getProductionTask } =
            await import("@/core/voice-twin/production-api");
          const updated = await getProductionTask(task.id);
          if (!cancelled) {
            setTask(updated);
            if (updated.status === "ready" || updated.status === "failed") {
              clearInterval(interval);
            }
          }
        } catch {
          // keep polling on transient errors
        }
      })();
    }, 2000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [task]);

  // ── Audio playback control ──────────────────────────────────────────

  const audioSrc =
    task?.status === "ready" ? getProductionStreamURL(task.id) : null;

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !audioSrc) return;

    const onTime = () => setAudioProgress(audio.currentTime);
    const onMeta = () => setAudioDuration(audio.duration);
    const onEnd = () => setPlaying(false);

    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("loadedmetadata", onMeta);
    audio.addEventListener("ended", onEnd);
    return () => {
      audio.removeEventListener("timeupdate", onTime);
      audio.removeEventListener("loadedmetadata", onMeta);
      audio.removeEventListener("ended", onEnd);
    };
  }, [audioSrc]);

  const togglePlay = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (playing) {
      audio.pause();
    } else {
      void audio.play();
    }
    setPlaying((p) => !p);
  }, [playing]);

  // ── Download ────────────────────────────────────────────────────────

  const downloadUrl =
    task?.status === "ready" ? getProductionDownloadURL(task.id) : null;

  const handleDownload = useCallback(() => {
    if (!downloadUrl) return;
    const a = document.createElement("a");
    // downloadUrl（getProductionDownloadURL）已含 backend base——不得再拼
    // getBackendBaseURL（曾双拼成 http://host:2026http://...，2026-07-24 P1）
    a.href = downloadUrl;
    a.download = `${docTitle ?? "narration"}.mp3`;
    a.click();
  }, [downloadUrl, docTitle]);

  // ── Progress bar ────────────────────────────────────────────────────

  const taskProgress = task?.progress ?? 0;
  const isProcessing =
    task && task.status !== "ready" && task.status !== "failed";

  // ── Range options ──────────────────────────────────────────────────

  const rangeOptions: {
    value: PreviewRange;
    label: string;
    disabled?: boolean;
  }[] = [
    { value: "full", label: "全文" },
    {
      value: "selection",
      label: "选区",
      disabled: !selection?.trim(),
    },
    {
      value: "scene",
      label: "场景",
      disabled: !isScript,
    },
  ];

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3 text-sm">
      {/* Voice selector */}
      <div>
        <label className="mb-1 flex items-center gap-1 text-xs text-[var(--muted-foreground)]">
          <Mic size={11} />
          音色选择
        </label>
        <VoiceSelector
          value={voiceId}
          onChange={setVoiceId}
          className="w-full"
        />
      </div>

      {/* Range selector */}
      <div>
        <label className="mb-1 block text-xs text-[var(--muted-foreground)]">
          试听范围
        </label>
        <div className="flex gap-1">
          {rangeOptions.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => !opt.disabled && setRange(opt.value)}
              disabled={opt.disabled}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs transition-colors",
                range === opt.value
                  ? "bg-[var(--primary)]/15 text-[var(--primary)]"
                  : "bg-[var(--secondary)]/50 text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
                opt.disabled && "cursor-not-allowed opacity-40",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Scene selector */}
        {range === "scene" && isScript && (
          <select
            value={sceneIdx}
            onChange={(e) => setSceneIdx(Number(e.target.value))}
            className="mt-2 w-full rounded-md border border-[var(--border)] bg-[var(--secondary)]/50 px-2.5 py-1.5 text-xs text-[var(--foreground)] focus:outline-none"
          >
            {scenes.map((s, i) => (
              <option key={i} value={i}>
                {s.title} ({s.estimatedDuration}s)
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Speed */}
      <div>
        <label className="mb-1 block text-xs text-[var(--muted-foreground)]">
          语速 {speed.toFixed(1)}x
        </label>
        <input
          type="range"
          min={0.5}
          max={2.0}
          step={0.1}
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          className="w-full accent-[var(--primary)]"
        />
      </div>

      {/* Text info */}
      <div className="rounded-md bg-[var(--secondary)]/30 px-2.5 py-1.5 text-xs text-[var(--muted-foreground)]">
        {charCount} 字
        {isScript && range === "full" && (
          <span className="ml-2">· 预计 {totalDuration}s</span>
        )}
        {isScript && range === "scene" && scenes[sceneIdx] && (
          <span className="ml-2">
            · 预计 {scenes[sceneIdx].estimatedDuration}s
          </span>
        )}
      </div>

      {/* Submit */}
      <Button
        size="sm"
        onClick={handleSubmit}
        disabled={!canSubmit}
        className="w-full"
      >
        {submitting ? (
          <>
            <Loader2 size={13} className="animate-spin" />
            提交中...
          </>
        ) : (
          <>
            <Volume2 size={13} />
            生成试听
          </>
        )}
      </Button>

      {/* Error */}
      {error && <p className="text-xs text-rose-500">{error}</p>}

      {/* Task progress */}
      {isProcessing && (
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
            <span>
              {task?.status === "synthesizing"
                ? "合成中..."
                : task?.status === "pending"
                  ? "排队中..."
                  : "处理中..."}
            </span>
            <span>{taskProgress}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-[var(--secondary)]">
            <div
              className="h-full rounded-full bg-[var(--primary)] transition-all"
              style={{ width: `${taskProgress}%` }}
            />
          </div>
        </div>
      )}

      {/* Task failed */}
      {task?.status === "failed" && (
        <p className="rounded-md bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-500">
          合成失败：{task.error_message ?? "未知错误"}
        </p>
      )}

      {/* Audio player */}
      {audioSrc && (
        <div className="space-y-2 rounded-md border border-[var(--border)] bg-[var(--secondary)]/30 p-2.5">
          <audio ref={audioRef} src={audioSrc} preload="metadata" hidden />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={togglePlay}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--primary)]/15 text-[var(--primary)]"
            >
              {playing ? <Pause size={13} /> : <Play size={13} />}
            </button>
            <div className="h-1 flex-1 rounded-full bg-[var(--secondary)]">
              <div
                className="h-1 rounded-full bg-[var(--primary)]"
                style={{
                  width: `${
                    audioDuration > 0
                      ? (audioProgress / audioDuration) * 100
                      : 0
                  }%`,
                }}
              />
            </div>
            <span className="text-[10px] text-[var(--muted-foreground)] tabular-nums">
              {formatTime(audioProgress)} / {formatTime(audioDuration)}
            </span>
          </div>

          {/* Download */}
          <button
            type="button"
            onClick={handleDownload}
            className="flex w-full items-center justify-center gap-1 rounded-md border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
          >
            <Download size={11} />
            下载 MP3
          </button>
        </div>
      )}
    </div>
  );
}
