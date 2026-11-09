"use client";

import { Pause, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

interface WaveformPreviewProps {
  /** URL to the audio file (blob URL or server URL). */
  audioUrl: string;
  className?: string;
}

export function WaveformPreview({ audioUrl, className }: WaveformPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const animFrameRef = useRef<number>(0);
  const amplitudesRef = useRef<number[]>([]);
  const [isPlaying, setIsPlaying] = useState(false);

  // Decode audio and draw static waveform
  useEffect(() => {
    if (!canvasRef.current || !audioUrl) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const displayWidth = canvas.clientWidth;
    const displayHeight = canvas.clientHeight;
    canvas.width = displayWidth * dpr;
    canvas.height = displayHeight * dpr;
    ctx.scale(dpr, dpr);

    let cancelled = false;
    const audioCtx = new AudioContext();

    fetch(audioUrl)
      .then((res) => res.arrayBuffer())
      .then((buffer) => audioCtx.decodeAudioData(buffer))
      .then((audioBuffer) => {
        if (cancelled) return;
        const channelData = audioBuffer.getChannelData(0);
        // Cap sample count for memory (4000 points ≈ 10min at 44100Hz)
        const maxSamples = 4000;
        const sampleCount = Math.min(maxSamples, channelData.length);
        const blockSize = Math.floor(channelData.length / sampleCount);
        const amps: number[] = [];

        for (let i = 0; i < sampleCount; i++) {
          let sum = 0;
          for (let j = 0; j < blockSize; j++) {
            sum += Math.abs(channelData[i * blockSize + j] ?? 0);
          }
          amps.push(sum / blockSize);
        }

        const max = Math.max(...amps, 0.01);
        amplitudesRef.current = amps.map((a) => a / max);
        drawWaveform(
          ctx,
          displayWidth,
          displayHeight,
          amplitudesRef.current,
          0,
        );
        void audioCtx.close();
      })
      .catch(() => {
        void audioCtx.close();
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioUrl]);

  const drawWaveform = useCallback(
    (
      ctx: CanvasRenderingContext2D,
      width: number,
      height: number,
      amps: number[],
      progressRatio: number,
    ) => {
      ctx.clearRect(0, 0, width, height);

      const barWidth = 2;
      const gap = 1;
      const barCount = Math.floor(width / (barWidth + gap));

      const idleGradient = ctx.createLinearGradient(0, 0, width, 0);
      idleGradient.addColorStop(0, "rgba(129, 140, 248, 0.5)");
      idleGradient.addColorStop(0.5, "rgba(192, 132, 252, 0.5)");
      idleGradient.addColorStop(1, "rgba(34, 211, 238, 0.5)");

      const activeGradient = ctx.createLinearGradient(0, 0, width, 0);
      activeGradient.addColorStop(0, "rgba(129, 140, 248, 0.9)");
      activeGradient.addColorStop(0.5, "rgba(192, 132, 252, 0.9)");
      activeGradient.addColorStop(1, "rgba(34, 211, 238, 0.9)");

      for (let i = 0; i < barCount; i++) {
        const sampleIndex = Math.floor((i * amps.length) / barCount);
        const value = amps[sampleIndex] ?? 0;
        const barHeight = Math.max(value * height * 0.8, 2);
        const x = i * (barWidth + gap);
        const y = (height - barHeight) / 2;
        const isPlayed = x / width <= progressRatio;

        ctx.fillStyle = isPlayed ? activeGradient : idleGradient;
        ctx.beginPath();
        ctx.roundRect(x, y, barWidth, barHeight, 1);
        ctx.fill();
      }
    },
    [],
  );

  // Animate progress indicator during playback
  useEffect(() => {
    if (!isPlaying) return;

    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const displayWidth = canvas.clientWidth;
    const displayHeight = canvas.clientHeight;

    const animate = () => {
      const audio = audioRef.current;
      if (audio?.duration) {
        const ratio = audio.currentTime / audio.duration;
        drawWaveform(
          ctx,
          displayWidth,
          displayHeight,
          amplitudesRef.current,
          ratio,
        );
      }
      animFrameRef.current = requestAnimationFrame(animate);
    };

    animFrameRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(animFrameRef.current);
  }, [isPlaying, drawWaveform]);

  const togglePlay = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
    } else {
      void audio.play();
    }
    setIsPlaying(!isPlaying);
  }, [isPlaying]);

  const handleEnded = useCallback(() => {
    setIsPlaying(false);
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (canvas && ctx) {
      drawWaveform(
        ctx,
        canvas.clientWidth,
        canvas.clientHeight,
        amplitudesRef.current,
        0,
      );
    }
  }, [drawWaveform]);

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <audio
        ref={audioRef}
        src={audioUrl}
        onEnded={handleEnded}
        preload="metadata"
      />
      <button
        type="button"
        onClick={togglePlay}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-500/20 text-indigo-500 transition-colors hover:bg-indigo-500/30"
      >
        {isPlaying ? (
          <Pause className="h-3 w-3" />
        ) : (
          <Play className="ml-0.5 h-3 w-3" />
        )}
      </button>
      <canvas
        ref={canvasRef}
        className="h-10 flex-1 cursor-pointer"
        style={{ width: "100%" }}
      />
    </div>
  );
}
