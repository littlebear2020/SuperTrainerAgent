"use client";

import { AudioLines, FileText, Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { PodcastCase } from "@/core/agents/showcase";
import { useI18n } from "@/core/i18n/hooks";

import { PromptButton } from "./showcase-cards";
import {
  formatDuration,
  localized,
  type ShowcaseLocale,
} from "./showcase-utils";

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

  const toggle = (item: PodcastCase) => {
    const audio = audioRef.current;
    if (!audio) return;
    if (
      activeId === item.id &&
      (playingId === item.id || loadingId === item.id)
    ) {
      requestRef.current += 1;
      audio.pause();
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
    void Promise.resolve(audio.play()).then(
      () => {
        if (!isCurrent()) return;
        setPlayingId(item.id);
        setLoadingId(null);
      },
      () => {
        if (!isCurrent()) return;
        setPlayingId(null);
        setLoadingId(null);
        setErrorId(item.id);
      },
    );
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
          requestRef.current += 1;
          setPlayingId(null);
          setLoadingId(null);
          setCurrentTime(0);
        }}
        onError={() => {
          const id = activeIdRef.current;
          if (!id) return;
          requestRef.current += 1;
          setPlayingId(null);
          setLoadingId(null);
          setErrorId(id);
          setCurrentTime(0);
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
              className="bg-card/70 border-border flex min-w-0 flex-col gap-3 rounded-2xl border p-4 shadow-sm backdrop-blur-sm transition duration-200 hover:-translate-y-0.5 hover:border-cyan-300 hover:shadow-md motion-reduce:hover:translate-y-0 dark:border-white/10 dark:bg-white/5 dark:hover:border-cyan-400/50"
            >
              <div className="flex items-start justify-between gap-3">
                <span className="text-[11px] font-medium text-cyan-700 dark:text-cyan-300">
                  {localized(item.tag, locale)}
                </span>
                <span className="text-muted-foreground text-[11px] tabular-nums">
                  {formatDuration(item.durationSeconds)}
                </span>
              </div>
              <div>
                <h3 className="text-foreground text-sm leading-6 font-semibold">
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
                  onClick={() => toggle(item)}
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-cyan-600 text-white shadow-sm transition hover:bg-cyan-500 focus-visible:ring-2 focus-visible:ring-cyan-500 focus-visible:outline-none"
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
                <div className="min-w-0 flex-1">
                  <progress
                    data-testid={`showcase-progress-${item.id}`}
                    aria-label={`${t.agents.showcase.listen}: ${title}`}
                    max={item.durationSeconds}
                    value={progress}
                    className="h-1.5 w-full accent-cyan-600"
                  />
                  <div className="text-muted-foreground mt-1 flex justify-between text-[10px] tabular-nums">
                    <span>{formatDuration(progress)}</span>
                    <span>{formatDuration(item.durationSeconds)}</span>
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {item.voices.map((voice) => (
                  <span
                    key={voice.en}
                    className="inline-flex items-center gap-1 rounded-full bg-cyan-50 px-2 py-1 text-[10px] text-cyan-800 dark:bg-cyan-400/10 dark:text-cyan-200"
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
              <div className="mt-auto flex items-center justify-between gap-3 pt-1">
                <button
                  type="button"
                  data-testid={`showcase-transcript-${item.id}`}
                  onClick={(event) => onTranscript(item, event.currentTarget)}
                  className="inline-flex min-h-9 items-center gap-1.5 rounded-full px-2 text-xs text-cyan-700 hover:bg-cyan-50 focus-visible:ring-2 focus-visible:ring-cyan-500 focus-visible:outline-none dark:text-cyan-300 dark:hover:bg-cyan-400/10"
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
            </article>
          );
        })}
      </div>
    </>
  );
}
