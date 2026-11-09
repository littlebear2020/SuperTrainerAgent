"use client";

import { Mic, Pause, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

import { AudioVisualizer } from "./audio-visualizer";

interface VoiceRecorderProps {
  onComplete: (file: File, duration: number) => void;
  minimumDuration?: number;
  maximumDuration?: number;
}

type RecorderState = "idle" | "recording" | "paused" | "stopped";

function getSupportedMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  for (const type of candidates) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

/**
 * 录音有效时长（秒）：墙钟 - 累计暂停；暂停未结算段（pauseStartMs>0）在停止时补扣。
 * 抽出为纯函数以便单测（评审跟进：暂停计时曾零覆盖）。
 */
export function computeRecordedSeconds(
  startTimeMs: number,
  endTimeMs: number,
  pausedTotalMs: number,
  pauseStartMs: number,
): number {
  const settledPausedMs =
    pausedTotalMs + (pauseStartMs > 0 ? endTimeMs - pauseStartMs : 0);
  return Math.max(
    0,
    Math.floor((endTimeMs - startTimeMs - settledPausedMs) / 1000),
  );
}

export function extFromMimeType(mimeType: string): string {
  // audio/mp4（Safari 唯一支持项）→ m4a：后端 SUPPORTED_FORMATS 无 "mp4"，
  // 直接给 mp4 扩展名会被 prescreen 拒收（Safari 录音克隆曾必败，2026-07-22 审计 P1#5）。
  if (mimeType.includes("webm")) return "webm";
  if (mimeType.includes("ogg")) return "ogg";
  return "m4a";
}

export function isRecordingSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    getSupportedMimeType() !== ""
  );
}

export function VoiceRecorder({
  onComplete,
  minimumDuration = 30,
  maximumDuration = 180,
}: VoiceRecorderProps) {
  const [state, setState] = useState<RecorderState>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startTimeRef = useRef<number>(0);
  const pausedTotalRef = useRef(0);
  const pauseStartRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);

  const cleanup = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
    }
    if (audioCtxRef.current) {
      void audioCtxRef.current.close();
    }
    setAnalyser(null);
    streamRef.current = null;
    mediaRecorderRef.current = null;
    audioCtxRef.current = null;
  }, []);

  useEffect(() => {
    return cleanup;
  }, [cleanup]);

  // PR-1：上移到 startRecording 之前——后者的 interval 到达 maximumDuration 时
  // 调用它，deps 须列它（原 [] 属漏报，且后向引用使 memoization 无法保留）。
  const stopRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (!recorder) return;

    recorder.onstop = () => {
      const mimeType = getSupportedMimeType();
      const ext = extFromMimeType(mimeType);
      const blob = new Blob(chunksRef.current, { type: mimeType });
      const file = new File([blob], `recording.${ext}`, { type: mimeType });
      const duration = computeRecordedSeconds(
        startTimeRef.current,
        Date.now(),
        pausedTotalRef.current,
        pauseStartRef.current,
      );
      onComplete(file, duration);
      cleanup();
      setState("stopped");
    };

    recorder.stop();
    if (timerRef.current) clearInterval(timerRef.current);
  }, [onComplete, cleanup]);

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Set up analyser for visualization
      const audioCtx = new AudioContext();
      const source = audioCtx.createMediaStreamSource(stream);
      const analyserNode = audioCtx.createAnalyser();
      analyserNode.fftSize = 256;
      source.connect(analyserNode);
      audioCtxRef.current = audioCtx;
      setAnalyser(analyserNode);

      const mimeType = getSupportedMimeType();
      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.start(1000); // collect data every second
      startTimeRef.current = Date.now();
      pausedTotalRef.current = 0;
      pauseStartRef.current = 0;
      setState("recording");

      timerRef.current = setInterval(() => {
        const secs = Math.floor(
          (Date.now() - startTimeRef.current - pausedTotalRef.current) / 1000,
        );
        setElapsed(secs);
        if (secs >= maximumDuration) {
          stopRecording();
        }
      }, 1000);

      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Microphone access denied");
    }
  }, [maximumDuration, stopRecording]);

  const pauseRecording = useCallback(() => {
    mediaRecorderRef.current?.pause();
    if (timerRef.current) clearInterval(timerRef.current);
    pauseStartRef.current = Date.now();
    setState("paused");
  }, []);

  const resumeRecording = useCallback(() => {
    mediaRecorderRef.current?.resume();
    if (pauseStartRef.current > 0) {
      pausedTotalRef.current += Date.now() - pauseStartRef.current;
      pauseStartRef.current = 0;
    }
    timerRef.current = setInterval(() => {
      setElapsed(
        Math.floor(
          (Date.now() - startTimeRef.current - pausedTotalRef.current) / 1000,
        ),
      );
    }, 1000);
    setState("recording");
  }, []);

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const progress = Math.min(elapsed / minimumDuration, 1);
  const canStop = elapsed >= minimumDuration;

  return (
    <div className="flex flex-col items-center gap-4">
      {error && (
        <div className="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-500">
          {error}
        </div>
      )}

      {/* Timer + progress */}
      <div className="flex flex-col items-center gap-1">
        <span
          className={cn(
            "font-mono text-2xl font-bold",
            state === "recording" ? "text-red-500" : "text-slate-500",
          )}
        >
          {formatTime(elapsed)}
        </span>
        <span className="text-xs text-slate-400">
          最少 {formatTime(minimumDuration)} / 最长{" "}
          {formatTime(maximumDuration)}
        </span>
        <div className="mt-1 h-1 w-40 overflow-hidden rounded-full bg-slate-200/40 dark:bg-slate-700/40">
          <div
            className={cn(
              "h-full rounded-full transition-all duration-500",
              canStop ? "bg-emerald-500" : "bg-indigo-500",
            )}
            style={{ width: `${Math.min(progress * 100, 100)}%` }}
          />
        </div>
      </div>

      {/* Visualizer */}
      <AudioVisualizer
        analyserNode={analyser}
        isActive={state === "recording"}
      />

      {/* Controls */}
      <div className="flex gap-3">
        {state === "idle" && (
          <button
            onClick={startRecording}
            className="flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 px-4 py-2.5 text-xs font-bold text-white transition-all hover:scale-105"
          >
            <Mic className="h-3.5 w-3.5" />
            开始录制
          </button>
        )}

        {state === "recording" && (
          <>
            <button
              onClick={pauseRecording}
              className="flex items-center gap-1.5 rounded-xl bg-slate-200/50 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-300/50"
            >
              <Pause className="h-3.5 w-3.5" />
              暂停
            </button>
            <button
              onClick={stopRecording}
              disabled={!canStop}
              className={cn(
                "flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-xs font-bold text-white transition-all",
                canStop
                  ? "bg-red-500 hover:bg-red-600"
                  : "cursor-not-allowed bg-slate-400 opacity-50",
              )}
            >
              <Square className="h-3.5 w-3.5" />
              停止录制
            </button>
          </>
        )}

        {state === "paused" && (
          <>
            <button
              onClick={resumeRecording}
              className="flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 px-4 py-2.5 text-xs font-bold text-white hover:scale-105"
            >
              <Mic className="h-3.5 w-3.5" />
              继续
            </button>
            <button
              onClick={stopRecording}
              disabled={!canStop}
              className={cn(
                "flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-xs font-bold text-white",
                canStop
                  ? "bg-red-500 hover:bg-red-600"
                  : "cursor-not-allowed bg-slate-400 opacity-50",
              )}
            >
              <Square className="h-3.5 w-3.5" />
              停止
            </button>
          </>
        )}
      </div>
    </div>
  );
}
