"use client";

import { AudioLines, CheckCircle2, FileText, Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { PodcastCase } from "@/core/agents/showcase";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { PromptButton } from "./showcase-cards";
import {
  formatDuration,
  localized,
  type ShowcaseLocale,
} from "./showcase-utils";

import styles from "./agent-showcase.module.css";

const waveHeights = [
  8, 14, 10, 23, 16, 30, 12, 21, 34, 17, 27, 13, 31, 19, 10, 24, 15, 28, 12, 21,
  8, 17, 11, 25, 15, 29, 9, 18, 13, 26, 10, 22,
];

export function ShowcaseAudioPlayer({
  items,
  locale,
  disabled,
  onUsePrompt,
  onTranscript,
}: {
  items: PodcastCase[];
  locale: ShowcaseLocale;
  disabled: boolean;
  onUsePrompt: (prompt: string) => void;
  onTranscript: (item: PodcastCase, trigger: HTMLButtonElement) => void;
}) {
  const { t } = useI18n();
  const audioRef = useRef<HTMLAudioElement>(null);
  const requestRef = useRef(0);
  const activeIdRef = useRef<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [errorId, setErrorId] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);

  useEffect(() => {
    const audio = audioRef.current;
    return () => {
      requestRef.current += 1;
      activeIdRef.current = null;
      audio?.pause();
    };
  }, []);

  const toggle = async (item: PodcastCase) => {
    const audio = audioRef.current;
    if (!audio) return;
    if (
      activeId === item.id &&
      (playingId === item.id || loadingId === item.id)
    ) {
      requestRef.current += 1;
      activeIdRef.current = null;
      audio.pause();
      setActiveId(null);
      setPlayingId(null);
      setLoadingId(null);
      setCurrentTime(0);
      return;
    }

    const request = ++requestRef.current;
    audio.pause();
    activeIdRef.current = item.id;
    setActiveId(item.id);
    setPlayingId(null);
    setLoadingId(item.id);
    setErrorId(null);
    setCurrentTime(0);
    audio.src = item.audio;
    const isCurrent = () =>
      requestRef.current === request && activeIdRef.current === item.id;
    try {
      await audio.play();
      if (!isCurrent()) return;
      setPlayingId(item.id);
      setLoadingId(null);
    } catch {
      if (!isCurrent()) return;
      setPlayingId(null);
      setLoadingId(null);
      setErrorId(item.id);
    }
  };

  return (
    <>
      <audio
        ref={audioRef}
        preload="none"
        onTimeUpdate={(event) =>
          setCurrentTime(event.currentTarget.currentTime)
        }
        onEnded={() => {
          const id = activeIdRef.current;
          const request = requestRef.current;
          if (!id) return;
          queueMicrotask(() => {
            if (activeIdRef.current !== id || requestRef.current !== request)
              return;
            requestRef.current += 1;
            setPlayingId(null);
            setLoadingId(null);
            setCurrentTime(0);
          });
        }}
        onError={() => {
          const id = activeIdRef.current;
          const request = requestRef.current;
          if (!id) return;
          queueMicrotask(() => {
            if (activeIdRef.current !== id || requestRef.current !== request)
              return;
            requestRef.current += 1;
            setPlayingId(null);
            setLoadingId(null);
            setErrorId(id);
            setCurrentTime(0);
          });
        }}
      />
      <div
        data-testid="agent-guide-cases"
        data-showcase
        className="grid gap-4 sm:grid-cols-2"
      >
        {items.map((item) => {
          const title = localized(item.title, locale);
          const isActive = activeId === item.id;
          const isPlaying = playingId === item.id;
          const progress = isActive ? currentTime : 0;
          return (
            <article
              key={item.id}
              data-testid={`showcase-case-${item.id}`}
              className={cn(
                "showcase-card border-border flex min-w-0 flex-col gap-3 rounded-[15px] border p-4 transition duration-200 hover:-translate-y-0.5 hover:border-violet-300 hover:shadow-md motion-reduce:hover:translate-y-0 dark:border-white/10 dark:hover:border-violet-400/50",
                isPlaying &&
                  "border-violet-300 shadow-[0_0_0_3px_rgb(139_92_246/0.08)] dark:border-violet-400/60",
                isPlaying && styles.isPlaying,
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <span className="showcase-tag rounded px-2 py-1 text-[10px] font-medium">
                  {localized(item.tag, locale)}
                </span>
                <span className="text-muted-foreground text-[11px] tabular-nums">
                  {formatDuration(item.durationSeconds)}
                </span>
              </div>
              <div>
                <h3 className="text-foreground text-[15px] leading-6 font-semibold">
                  {title}
                </h3>
                <p className="text-muted-foreground mt-1 line-clamp-3 text-xs leading-5">
                  {localized(item.description, locale)}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  data-testid={`showcase-play-${item.id}`}
                  aria-label={`${isPlaying ? t.agents.showcase.pause : t.agents.showcase.play} ${title}`}
                  onClick={() => void toggle(item)}
                  className={cn(
                    "flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-neutral-900 text-white shadow-sm transition hover:bg-violet-600 focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:outline-none dark:bg-neutral-100 dark:text-neutral-900",
                    isPlaying && "bg-violet-600 dark:bg-violet-500",
                  )}
                >
                  {isPlaying ? (
                    <Pause
                      className="h-4 w-4"
                      fill="currentColor"
                      aria-hidden="true"
                    />
                  ) : (
                    <Play
                      className="ml-0.5 h-4 w-4"
                      fill="currentColor"
                      aria-hidden="true"
                    />
                  )}
                </button>
                <div className="relative min-w-0 flex-1">
                  <div
                    data-testid={`showcase-waveform-${item.id}`}
                    aria-hidden="true"
                    className={styles.waveformBars}
                  >
                    {waveHeights.map((height, index) => (
                      <i
                        key={index}
                        style={{
                          height,
                          animationDelay: `${index * 35}ms`,
                        }}
                      />
                    ))}
                  </div>
                  <progress
                    data-testid={`showcase-progress-${item.id}`}
                    aria-label={`${t.agents.showcase.listen}: ${title}`}
                    max={item.durationSeconds}
                    value={progress}
                    className="absolute inset-x-0 bottom-0 h-0.5 w-full accent-violet-600"
                  />
                </div>
                <span className="text-muted-foreground shrink-0 text-[10px] tabular-nums">
                  {formatDuration(
                    progress > 0 ? progress : item.durationSeconds,
                  )}
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {item.voices.map((voice) => (
                  <span
                    key={voice.en}
                    className="text-muted-foreground border-border inline-flex items-center gap-1 rounded border px-2 py-1 text-[10px]"
                  >
                    <AudioLines className="h-3 w-3" aria-hidden="true" />
                    {localized(voice, locale)}
                  </span>
                ))}
              </div>
              {errorId === item.id ? (
                <p
                  role="status"
                  className="text-xs text-rose-600 dark:text-rose-300"
                >
                  {t.agents.showcase.playbackError}
                </p>
              ) : null}
              <div className="border-border/80 mt-auto flex items-center justify-between gap-3 border-t pt-1">
                <button
                  type="button"
                  data-testid={`showcase-transcript-${item.id}`}
                  onClick={(event) => onTranscript(item, event.currentTarget)}
                  className="showcase-accent-text inline-flex min-h-9 items-center gap-1.5 rounded-md px-2 text-xs hover:bg-violet-50 focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:outline-none dark:hover:bg-violet-400/10"
                >
                  <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                  {t.agents.showcase.transcript}
                </button>
                <PromptButton
                  item={item}
                  title={item.title}
                  locale={locale}
                  disabled={disabled}
                  onUsePrompt={onUsePrompt}
                />
              </div>
              <p className="text-muted-foreground flex items-start gap-1.5 pt-1 text-[10px] leading-4">
                <CheckCircle2
                  className="showcase-accent-text mt-0.5 h-3 w-3 shrink-0"
                  aria-hidden="true"
                />
                {localized(item.source, locale)}
              </p>
            </article>
          );
        })}
      </div>
    </>
  );
}
