"use client";

import { ArrowRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { HOME_COURSE_ITEMS, type HomeCourseItem } from "./home-course-catalog";
import { useMediaQuery } from "./use-media-query";

function CourseMedia({
  item,
  preview,
  failed,
  posterFailed,
  onError,
  onPlaying,
  onPosterError,
}: {
  item: HomeCourseItem;
  preview: boolean;
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
  const showVideo = item.media.kind === "video" && preview && !failed;

  useEffect(() => {
    if (!showVideo) {
      playRequestedRef.current = false;
      return;
    }
    if (playRequestedRef.current) return;
    playRequestedRef.current = true;
    const video = videoRef.current;
    if (!video) return;

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
      video.pause();
    };
  }, [showVideo, item.media.src, onError, onPlaying]);

  if (posterFailed && item.media.kind === "image") {
    return <div className="aspect-video bg-slate-100 dark:bg-slate-900" />;
  }

  return (
    <div className="relative aspect-video overflow-hidden bg-slate-100 dark:bg-slate-900">
      {!posterFailed ? (
        <img
          src={poster ?? item.media.src}
          alt={copy.mediaAlt}
          className={cn(
            "h-full w-full object-contain",
            showVideo && "invisible",
          )}
          loading="lazy"
          onError={onPosterError}
        />
      ) : null}
      {showVideo ? (
        <video
          ref={videoRef}
          className="absolute inset-0 h-full w-full object-contain"
          src={item.media.src}
          poster={poster}
          data-course-item={item.id}
          muted
          loop
          playsInline
          preload="metadata"
          aria-label={copy.mediaAlt}
          onError={onError}
        />
      ) : null}
      {failed && item.media.kind === "video" ? (
        <div
          role="status"
          className="bg-background/90 pointer-events-none absolute inset-0 flex items-center justify-center p-4 text-center text-sm"
        >
          {t.workspace.home.playbackUnavailable}
        </div>
      ) : null}
    </div>
  );
}

function CourseEntryCard({
  item,
  preview,
  failed,
  posterFailed,
  previewAllowed,
  onSelect,
  setFailedMedia,
  setFailedPosters,
}: {
  item: HomeCourseItem;
  preview: boolean;
  failed: boolean;
  posterFailed: boolean;
  previewAllowed: {
    enabled: boolean;
    onMouseEnter: (id: string) => void;
    onMouseLeave: () => void;
    onFocus: (id: string) => void;
    onBlur: () => void;
  };
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

  return (
    <button
      type="button"
      data-course-entry={item.id}
      aria-label={`${copy.title}：${copy.description}`}
      onClick={() => onSelect(item)}
      onMouseEnter={() => previewAllowed.onMouseEnter(item.id)}
      onMouseLeave={previewAllowed.onMouseLeave}
      onFocus={() => previewAllowed.onFocus(item.id)}
      onBlur={previewAllowed.onBlur}
      className={cn(
        "group bg-background flex h-full min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-200/80 text-left shadow-sm",
        previewAllowed.enabled &&
          "transition-[transform,box-shadow,border-color] duration-200 hover:-translate-y-1 hover:border-indigo-200 hover:shadow-xl",
      )}
    >
      <CourseMedia
        item={item}
        preview={preview}
        failed={failed}
        posterFailed={posterFailed}
        onError={onError}
        onPlaying={onPlaying}
        onPosterError={onPosterError}
      />
      <span className="flex flex-1 flex-col p-4 sm:p-5">
        <span className="flex items-center justify-between gap-3">
          <span className="min-w-0 text-base font-semibold">{copy.title}</span>
          <span
            aria-hidden="true"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition-colors group-hover:bg-indigo-600 group-hover:text-white"
          >
            <ArrowRight className="h-3.5 w-3.5" />
          </span>
        </span>
        <span className="text-muted-foreground mt-2 flex-1 text-sm leading-6">
          {copy.description}
        </span>
      </span>
    </button>
  );
}

export function HomeCourseEntry({
  onSelect,
}: {
  onSelect: (item: HomeCourseItem) => void;
}) {
  const { t } = useI18n();
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [failedMedia, setFailedMedia] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [failedPosters, setFailedPosters] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [pageVisible, setPageVisible] = useState(() =>
    typeof document === "undefined" ? false : !document.hidden,
  );
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleMotionPreferenceChange = useCallback(
    () => setPreviewId(null),
    [],
  );
  const coarsePointer = useMediaQuery(
    "(pointer: coarse)",
    handleMotionPreferenceChange,
  );
  const reducedMotion = useMediaQuery(
    "(prefers-reduced-motion: reduce)",
    handleMotionPreferenceChange,
  );
  const previewAllowed = !coarsePointer && !reducedMotion && pageVisible;
  const activePreviewId = previewAllowed ? previewId : null;

  useEffect(() => {
    const handleVisibility = () => {
      setPageVisible(!document.hidden);
      setPreviewId(null);
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibility);
  }, []);

  useEffect(() => {
    if (!previewAllowed && hoverTimer.current) {
      clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
  }, [previewAllowed]);

  useEffect(() => {
    return () => {
      if (hoverTimer.current) clearTimeout(hoverTimer.current);
    };
  }, []);

  const clearHoverTimer = () => {
    if (hoverTimer.current) {
      clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
  };

  const resetFailure = (id: string) => {
    setFailedMedia((current) => {
      if (!current.has(id)) return current;
      const next = new Set(current);
      next.delete(id);
      return next;
    });
  };

  const showPreview = (id: string) => {
    if (!previewAllowed) return;
    clearHoverTimer();
    if (activePreviewId !== id) resetFailure(id);
    setPreviewId(id);
  };

  const hidePreview = () => {
    clearHoverTimer();
    setPreviewId(null);
  };

  const queuePreview = (id: string) => {
    if (!previewAllowed) return;
    clearHoverTimer();
    hoverTimer.current = setTimeout(() => {
      hoverTimer.current = null;
      if (activePreviewId !== id) resetFailure(id);
      setPreviewId(id);
    }, 200);
  };

  return (
    <section aria-label={t.workspace.home.createHeading}>
      <div className="grid auto-rows-fr grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {HOME_COURSE_ITEMS.map((item) => (
          <CourseEntryCard
            key={item.id}
            item={item}
            preview={activePreviewId === item.id}
            failed={failedMedia.has(item.id)}
            posterFailed={failedPosters.has(item.id)}
            previewAllowed={{
              enabled: previewAllowed,
              onMouseEnter: queuePreview,
              onMouseLeave: hidePreview,
              onFocus: showPreview,
              onBlur: hidePreview,
            }}
            onSelect={onSelect}
            setFailedMedia={setFailedMedia}
            setFailedPosters={setFailedPosters}
          />
        ))}
      </div>
    </section>
  );
}
