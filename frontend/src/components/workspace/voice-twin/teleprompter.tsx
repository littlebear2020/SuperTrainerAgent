"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

interface TeleprompterProps {
  text: string;
  speed?: number; // scroll speed multiplier, default 1.0
  isRecording: boolean;
  className?: string;
}

export function Teleprompter({
  text,
  speed = 1.0,
  isRecording,
  className,
}: TeleprompterProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [customSpeed, setCustomSpeed] = useState(speed);

  // Auto-scroll when recording and not paused
  useEffect(() => {
    if (!isRecording || isPaused || !containerRef.current) return;

    const container = containerRef.current;
    let animFrame: number;
    let lastTime = 0;

    const scroll = (timestamp: number) => {
      if (lastTime === 0) lastTime = timestamp;
      const delta = timestamp - lastTime;
      lastTime = timestamp;

      // Scroll ~30px per second at speed=1.0
      container.scrollTop += (delta / 1000) * 30 * customSpeed;

      if (
        container.scrollTop <
        container.scrollHeight - container.clientHeight
      ) {
        animFrame = requestAnimationFrame(scroll);
      }
    };

    animFrame = requestAnimationFrame(scroll);
    return () => cancelAnimationFrame(animFrame);
  }, [isRecording, isPaused, customSpeed]);

  const togglePause = useCallback(() => {
    setIsPaused((p) => !p);
  }, []);

  // Split text into lines for highlighting
  const lines = text.split(/[。！？\n.!?]+/).filter((l) => l.trim());

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {/* Speed control */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-400">滚动速度</span>
        <div className="flex items-center gap-2">
          <input
            type="range"
            min="0.3"
            max="3.0"
            step="0.1"
            value={customSpeed}
            onChange={(e) => setCustomSpeed(Number(e.target.value))}
            className="w-20"
            disabled={!isRecording}
          />
          <span className="w-8 text-right font-mono text-xs text-slate-400">
            {customSpeed.toFixed(1)}x
          </span>
        </div>
      </div>

      {/* Scrolling text container */}
      <div
        ref={containerRef}
        onClick={togglePause}
        className={cn(
          "h-32 cursor-pointer overflow-y-auto rounded-xl border p-3 text-xs leading-relaxed",
          "border-slate-200/50 bg-white/30 dark:border-slate-700/50 dark:bg-slate-900/30",
          isPaused && "opacity-60",
        )}
      >
        {lines.map((line, i) => (
          <p key={i} className="mb-1 last:mb-0">
            {line}
          </p>
        ))}
      </div>

      {isPaused && isRecording && (
        <p className="text-center text-xs text-slate-400">
          点击文本区域继续滚动
        </p>
      )}
    </div>
  );
}

/** Default teleprompter texts in multiple languages */
export const DEFAULT_TELEPROMPTER_TEXTS: Record<string, string> = {
  zh: "大家好，欢迎来到今天的课程。人工智能正在深刻改变我们的生活方式和工作方式。从自然语言处理到计算机视觉，从自动驾驶到智能医疗，AI技术正在各个领域展现出巨大的潜力。今天我们将一起探索这个令人兴奋的领域，了解深度学习的基本原理和实际应用。让我们开始这段精彩的学习旅程吧。",
  en: "Hello everyone, welcome to today's course. Artificial intelligence is profoundly changing the way we live and work. From natural language processing to computer vision, from autonomous driving to intelligent healthcare, AI technology is showing tremendous potential across various fields. Today we will explore this exciting area together and understand the basic principles and practical applications of deep learning. Let's begin this wonderful learning journey.",
};
