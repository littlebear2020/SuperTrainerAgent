"use client";

import { Pause, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

interface AudioPlayerProps {
  src: string;
  className?: string;
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function AudioPlayer({ src, className }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onTimeUpdate = () => setProgress(audio.currentTime);
    const onLoadedMetadata = () => setDuration(audio.duration);
    const onEnded = () => setPlaying(false);

    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("loadedmetadata", onLoadedMetadata);
    audio.addEventListener("ended", onEnded);

    return () => {
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("loadedmetadata", onLoadedMetadata);
      audio.removeEventListener("ended", onEnded);
    };
  }, [src]);

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;

    if (playing) {
      audio.pause();
      setPlaying(false);
    } else {
      // 仅在 play() 成功后切图标——先乐观置 true 再被 reject 回退会让播放/暂停图标闪一帧；
      // reject（如 demo 404）时保持非播放态。
      audio
        .play()
        .then(() => setPlaying(true))
        .catch(() => setPlaying(false));
    }
  }, [playing]);

  const pct = duration > 0 ? (progress / duration) * 100 : 0;

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <audio ref={audioRef} src={src} preload="metadata" className="hidden" />

      <button
        type="button"
        onClick={toggle}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-500/10"
      >
        {playing ? (
          <Pause className="h-3.5 w-3.5" />
        ) : (
          <Play className="h-3.5 w-3.5" />
        )}
      </button>

      <div className="h-1 w-full rounded-full bg-slate-200">
        <div
          className="h-1 rounded-full bg-indigo-500"
          style={{ width: `${pct}%` }}
        />
      </div>

      <span className="text-xs text-slate-400">{formatTime(progress)}</span>
      <span className="text-xs text-slate-400">{formatTime(duration)}</span>
    </div>
  );
}
