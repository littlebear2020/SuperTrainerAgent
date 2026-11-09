"use client";

import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Pause,
  Play,
  Volume2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useGenerateAudio } from "@/core/video-gen/hooks";

interface AudioPreviewBarProps {
  taskId: string;
  audioReady: boolean;
  audioError?: string;
  /**
   * Plan B（2026-10-19）：成功降级提示（「已使用备用音色（请求 X，实际 Y）」）——
   * 只由后端「实际音色已归位」sidecar 记录生成（storyboard.audio_degradation_warning）。
   * audioReady 时琥珀条展示；audioError 分支不显示（降级后失败≠成功降级）。
   */
  degradationWarning?: string | null;
  totalDuration?: number;
  /** Number of frames — drives "试听全部" (sequential play frame 0..N-1). */
  frameCount?: number;
  /** Called when the user clicks "generate audio" — parent uses this to enable polling. */
  onGenerateStart?: () => void;
  /** Called when generation fails — parent uses this to stop polling. */
  onGenerateError?: () => void;
}

/**
 * Audio preview status bar for draft tasks.
 *
 * Shows audio generation status and a "generate" button when audio is not ready.
 * After triggering generation, the component maintains a local `isGenerating`
 * state and enables short-term polling on the storyboard query until
 * `audio_ready` flips to true, ensuring the UI reflects completion without
 * requiring a manual page refresh.
 *
 * When audio is ready, a play/pause button streams frame 0's narration via
 * `GET /api/video/tasks/{id}/audio?frame=0` (Range-supported). Per-frame and
 * concatenated full-narration playback are follow-ups; this covers the core
 * "user can actually hear the narration" gap.
 */
export function AudioPreviewBar({
  taskId,
  audioReady,
  audioError,
  degradationWarning,
  totalDuration,
  frameCount = 0,
  onGenerateStart,
  onGenerateError,
}: AudioPreviewBarProps) {
  const generateMut = useGenerateAudio(taskId);

  // Local state: stays true from the moment generation is triggered until
  // the parent prop `audioReady` flips to true (detected via polling).
  const [isGenerating, setIsGenerating] = useState(false);

  // Playback: stream narration via the audio endpoint. src is set imperatively
  // (not bound in JSX) so 试听首帧 (frame 0) and 试听全部 (frame 0..N-1 sequential)
  // can swap src without React re-render resetting it.
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [playAllActive, setPlayAllActive] = useState(false);
  const playAllFrameRef = useRef(0);

  const playFrame = (frame: number) => {
    const el = audioRef.current;
    if (!el) return;
    el.src = `/api/video/tasks/${taskId}/audio?frame=${frame}`;
    el.load();
    // B3（2026-10-07 遗留批 P2-2）+ B3 复审 F1：试听按钮只在本组件 audioReady 分支
    // 渲染 → play() 拒绝时 payload 必为 audio_ready=true，generate-audio 会被后端
    // already_ready 短路（action 恒 no-op）——只解释不引导；修前是静默吞错、play-all
    // 链无声停在该帧。
    void el
      .play()
      .catch(() => toast.info(`第 ${frame + 1} 帧旁白已修改，音频待更新`));
  };

  // 试听首帧：退出 play-all 模式，单帧 frame 0 播放/暂停切换
  const handlePlayFirst = () => {
    setPlayAllActive(false);
    const el = audioRef.current;
    if (!el) return;
    if (playing) {
      el.pause();
    } else {
      playFrame(0);
    }
  };

  // 试听全部：从 frame 0 顺序播到 N-1（onEnded 驱动切帧）
  const handlePlayAll = () => {
    if (playAllActive) {
      setPlayAllActive(false);
      audioRef.current?.pause();
    } else if (frameCount > 0) {
      setPlayAllActive(true);
      playAllFrameRef.current = 0;
      playFrame(0);
    }
  };

  const handleEnded = () => {
    setPlaying(false);
    if (playAllActive && playAllFrameRef.current < frameCount - 1) {
      playAllFrameRef.current += 1;
      playFrame(playAllFrameRef.current);
    } else if (playAllActive) {
      setPlayAllActive(false); // 全部播完
    }
  };

  // Clear local generating flag once the backend confirms audio is ready.
  useEffect(() => {
    if (audioReady && isGenerating) setIsGenerating(false);
  }, [audioReady, isGenerating]);

  // Error recovery: if the API call fails, reset state and stop polling.
  useEffect(() => {
    if (generateMut.isError && isGenerating) {
      setIsGenerating(false);
      onGenerateError?.();
    }
  }, [generateMut.isError, isGenerating, onGenerateError]);

  // If the mutation itself is still pending (HTTP request in flight), treat
  // that as generating too. audioError stops the spinner (backend reported failure).
  const showGenerating = (isGenerating || generateMut.isPending) && !audioError;

  return (
    <div className="bg-card flex items-center gap-3 rounded-lg border px-4 py-2">
      {audioReady ? (
        <>
          <CheckCircle2 className="h-4 w-4 text-green-500" />
          <span className="text-muted-foreground text-xs">
            旁白音频已就绪
            {totalDuration ? ` · ${Math.round(totalDuration)}秒` : ""}
          </span>
          {degradationWarning && (
            <span
              data-testid="audio-degradation-warning"
              className="flex-1 truncate text-xs text-amber-600"
              title={degradationWarning}
            >
              {degradationWarning}
            </span>
          )}
          <audio
            ref={audioRef}
            preload="none"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={handleEnded}
          />
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={handlePlayFirst}
          >
            {playing && !playAllActive ? (
              <Pause className="mr-1 h-3 w-3" />
            ) : (
              <Play className="mr-1 h-3 w-3" />
            )}
            试听首帧
          </Button>
          {frameCount > 1 && (
            <Button
              variant={playAllActive ? "default" : "outline"}
              size="sm"
              className="h-7 text-xs"
              onClick={handlePlayAll}
            >
              {playAllActive ? (
                <Pause className="mr-1 h-3 w-3" />
              ) : (
                <Play className="mr-1 h-3 w-3" />
              )}
              {playAllActive ? "停止播放" : "试听全部"}
            </Button>
          )}
        </>
      ) : audioError ? (
        <>
          <AlertCircle className="h-4 w-4 shrink-0 text-red-500" />
          <span className="flex-1 text-xs text-red-500">{audioError}</span>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => {
              generateMut.reset();
              setIsGenerating(true);
              onGenerateStart?.();
              generateMut.mutate();
            }}
          >
            重试
          </Button>
        </>
      ) : (
        <>
          <Volume2 className="text-muted-foreground h-4 w-4" />
          <span className="text-muted-foreground text-xs">
            {showGenerating
              ? "正在生成旁白音频..."
              : generateMut.isError
                ? "音频生成失败"
                : "旁白音频未准备"}
          </span>
          {generateMut.isError && (
            <span className="text-xs text-red-500">
              {generateMut.error?.message}
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => {
              generateMut.reset();
              setIsGenerating(true);
              onGenerateStart?.();
              generateMut.mutate();
            }}
            disabled={showGenerating}
          >
            {showGenerating ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : null}
            {generateMut.isError ? "重试" : "准备音频"}
          </Button>
        </>
      )}
    </div>
  );
}
