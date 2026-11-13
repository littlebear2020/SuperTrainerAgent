"use client";

import { ArrowRight, FileText, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import {
  HOME_COURSE_ITEMS,
  type HomeCourseGroup,
  type HomeCourseItem,
} from "./home-course-catalog";
import { useMediaQuery } from "./use-media-query";

export function CourseMedia({
  item,
  playbackAllowed,
  failed,
  posterFailed,
  onError,
  onPlaying,
  onPosterError,
}: {
  item: HomeCourseItem;
  playbackAllowed: boolean;
  failed: boolean;
  posterFailed: boolean;
  onError: () => void;
  onPlaying: () => void;
  onPosterError: () => void;
}) {
  const { t } = useI18n();
  const copy = t.workspace.home.courseItems[item.id];
  const poster = item.media.poster;
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const playRequestedRef = useRef(false);
  const showVideo = item.media.kind === "video" && playbackAllowed && !failed;

  useEffect(() => {
    if (!showVideo) {
      playRequestedRef.current = false;
      return;
    }
    if (playRequestedRef.current) return;
    const video = videoRef.current;
    if (!video) return;
    video.playsInline = true;
    playRequestedRef.current = true;

    let cancelled = false;
    const playPromise = video.play();
    void playPromise
      .then(() => {
        if (!cancelled) onPlaying();
      })
      .catch((error: unknown) => {
        if (
          cancelled ||
          (error instanceof DOMException && error.name === "AbortError")
        ) {
          return;
        }
        onError();
      });
    return () => {
      cancelled = true;
      playRequestedRef.current = false;
      video.pause();
    };
  }, [showVideo, item.media.src, onError, onPlaying]);

  return (
    <div className="relative aspect-video overflow-hidden bg-slate-100 dark:bg-slate-900">
      {!posterFailed && (poster || item.media.kind === "image") ? (
        <img
          src={poster ?? item.media.src}
          alt={copy.mediaAlt}
          className={cn("h-full w-full object-cover", showVideo && "invisible")}
          loading="lazy"
          onError={onPosterError}
        />
      ) : null}
      {showVideo ? (
        <video
          ref={(node) => {
            videoRef.current = node;
          }}
          className="absolute inset-0 h-full w-full object-cover"
          src={item.media.src}
          poster={poster}
          data-course-item={item.id}
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          aria-label={copy.mediaAlt}
          onError={onError}
        />
      ) : null}
      <div className="pointer-events-none absolute top-3 left-3 z-10">
        <span
          data-media-label
          className="inline-flex rounded-full border border-indigo-100 bg-white/90 px-2.5 py-1 text-[10px] font-medium text-slate-600 shadow-sm"
        >
          {copy.mediaLabel}
        </span>
      </div>
    </div>
  );
}

function CourseEntryCard({
  item,
  playbackAllowed,
  failed,
  posterFailed,
  onSelect,
  setFailedMedia,
  setFailedPosters,
}: {
  item: HomeCourseItem;
  playbackAllowed: boolean;
  failed: boolean;
  posterFailed: boolean;
  onSelect: (item: HomeCourseItem) => void;
  setFailedMedia: (
    updater: (current: ReadonlySet<string>) => ReadonlySet<string>,
  ) => void;
  setFailedPosters: (
    updater: (current: ReadonlySet<string>) => ReadonlySet<string>,
  ) => void;
}) {
  const { t } = useI18n();
  const copy = t.workspace.home.courseItems[item.id];
  const cardRef = useRef<HTMLButtonElement | null>(null);
  const onError = useCallback(() => {
    setFailedMedia((current) => new Set(current).add(item.id));
  }, [item.id, setFailedMedia]);
  const onPlaying = useCallback(() => {
    setFailedMedia((current) => {
      const next = new Set(current);
      next.delete(item.id);
      return next;
    });
  }, [item.id, setFailedMedia]);
  const onPosterError = useCallback(() => {
    setFailedPosters((current) => new Set(current).add(item.id));
  }, [item.id, setFailedPosters]);
  const onRetry = useCallback(() => {
    setFailedMedia((current) => {
      const next = new Set(current);
      next.delete(item.id);
      return next;
    });
    queueMicrotask(() => cardRef.current?.focus());
  }, [item.id, setFailedMedia]);

  return (
    <article className="group bg-background relative flex h-full min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-200/80 shadow-sm transition-[transform,box-shadow,border-color] duration-200 hover:-translate-y-1 hover:border-indigo-200 hover:shadow-xl">
      <button
        ref={cardRef}
        type="button"
        data-course-entry={item.id}
        aria-label={`${copy.title}：${copy.description}`}
        onClick={() => onSelect(item)}
        className="flex h-full w-full flex-col text-left"
      >
        <CourseMedia
          item={item}
          playbackAllowed={playbackAllowed}
          failed={failed}
          posterFailed={posterFailed}
          onError={onError}
          onPlaying={onPlaying}
          onPosterError={onPosterError}
        />
        <span className="flex flex-1 flex-col p-4 sm:p-5">
          <span className="flex items-center justify-between gap-3">
            <span className="min-w-0 text-base font-semibold">
              {copy.title}
            </span>
            <span
              aria-hidden="true"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-violet-100 text-violet-600 transition-colors group-hover:bg-violet-600 group-hover:text-white"
            >
              <ArrowRight className="h-3.5 w-3.5" />
            </span>
          </span>
          <span className="text-muted-foreground mt-2 flex-1 text-sm leading-6">
            {copy.description}
          </span>
          <span className="mt-3 flex flex-wrap gap-1.5 border-t border-slate-100 pt-3">
            {copy.tags.map((tag) => (
              <span
                key={tag}
                data-course-tag
                className="rounded-md bg-violet-50 px-2 py-1 text-[10px] font-medium whitespace-nowrap text-violet-600"
              >
                {tag}
              </span>
            ))}
          </span>
        </span>
      </button>
      {failed && item.media.kind === "video" ? (
        <div
          role="status"
          className="bg-background/90 pointer-events-none absolute inset-x-0 top-0 z-20 flex aspect-video flex-col items-center justify-center gap-3 p-4 text-center text-sm"
        >
          <span>{t.workspace.home.playbackUnavailable}</span>
          <button
            type="button"
            data-course-retry={item.id}
            className="pointer-events-auto rounded-full bg-violet-100 px-3 py-1.5 text-xs font-medium text-violet-700"
            onClick={onRetry}
          >
            {t.workspace.works.retry}
          </button>
        </div>
      ) : null}
    </article>
  );
}

const GROUPS: readonly HomeCourseGroup[] = ["video", "studio"];

export function HomeCourseEntry({
  onSelect,
}: {
  onSelect: (item: HomeCourseItem) => void;
}) {
  const { t } = useI18n();
  const [failedMedia, setFailedMedia] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [failedPosters, setFailedPosters] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [pageVisible, setPageVisible] = useState(() =>
    typeof document === "undefined" ? false : !document.hidden,
  );
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const playbackAllowed = !reducedMotion && pageVisible;

  useEffect(() => {
    const handleVisibility = () => setPageVisible(!document.hidden);
    document.addEventListener("visibilitychange", handleVisibility);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibility);
  }, []);

  return (
    <div className="space-y-10">
      {GROUPS.map((group) => {
        const copy = t.workspace.home.groups[group];
        return (
          <section key={group} data-course-group={group}>
            <header className="mb-4 flex items-center gap-3">
              <span
                aria-hidden="true"
                className={cn(
                  "flex size-10 items-center justify-center rounded-xl",
                  group === "video"
                    ? "bg-violet-100 text-violet-700"
                    : "bg-teal-50 text-teal-700",
                )}
              >
                {group === "video" ? (
                  <Play className="size-4" />
                ) : (
                  <FileText className="size-4" />
                )}
              </span>
              <div>
                <div className="text-[9px] font-bold tracking-[0.13em] text-slate-400">
                  {copy.kicker}
                </div>
                <h2 className="text-xl font-bold tracking-tight">
                  {copy.title}
                </h2>
              </div>
            </header>
            <div className="grid auto-rows-fr grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {HOME_COURSE_ITEMS.filter((item) => item.group === group).map(
                (item) => (
                  <CourseEntryCard
                    key={item.id}
                    item={item}
                    playbackAllowed={playbackAllowed}
                    failed={failedMedia.has(item.id)}
                    posterFailed={failedPosters.has(item.id)}
                    onSelect={onSelect}
                    setFailedMedia={setFailedMedia}
                    setFailedPosters={setFailedPosters}
                  />
                ),
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
