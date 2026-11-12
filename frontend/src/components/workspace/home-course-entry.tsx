"use client";

import { ArrowRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { HOME_COURSE_ITEMS, type HomeCourseItem } from "./home-course-catalog";

function CourseMedia({
  item,
  preview,
  failed,
  onError,
}: {
  item: HomeCourseItem;
  preview: boolean;
  failed: boolean;
  onError: () => void;
}) {
  const { t } = useI18n();
  const copy = t.workspace.home.courseItems[item.id];
  const poster = item.media.poster;

  if (failed && item.media.kind === "image") {
    return <div className="aspect-video bg-slate-100 dark:bg-slate-900" />;
  }

  const showVideo = item.media.kind === "video" && preview && !failed;

  return (
    <div className="relative aspect-video overflow-hidden bg-slate-100 dark:bg-slate-900">
      <img
        src={poster ?? item.media.src}
        alt={copy.mediaAlt}
        className={cn("h-full w-full object-contain", showVideo && "invisible")}
        loading="lazy"
        onError={onError}
      />
      {showVideo ? (
        <video
          autoPlay
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

export function HomeCourseEntry({
  onSelect,
}: {
  onSelect: (item: HomeCourseItem) => void;
}) {
  const { t } = useI18n();
  const [coarsePointer, setCoarsePointer] = useState(
    () => window.matchMedia("(pointer: coarse)").matches,
  );
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [pageVisible, setPageVisible] = useState(() => !document.hidden);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [failedMedia, setFailedMedia] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewAllowed = !coarsePointer && !reducedMotion && pageVisible;
  const activePreviewId = previewAllowed ? previewId : null;

  useEffect(() => {
    const coarse = window.matchMedia("(pointer: coarse)");
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handleCoarse = () => {
      setCoarsePointer(coarse.matches);
      setPreviewId(null);
    };
    const handleReduced = () => {
      setReducedMotion(reduced.matches);
      setPreviewId(null);
    };
    const handleVisibility = () => {
      setPageVisible(!document.hidden);
      setPreviewId(null);
    };
    coarse.addEventListener("change", handleCoarse);
    reduced.addEventListener("change", handleReduced);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      coarse.removeEventListener("change", handleCoarse);
      reduced.removeEventListener("change", handleReduced);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
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

  const showPreview = (id: string) => {
    if (!previewAllowed) return;
    clearHoverTimer();
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
      setPreviewId(id);
    }, 200);
  };

  return (
    <section aria-label={t.workspace.home.createHeading}>
      <div className="grid auto-rows-fr grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {HOME_COURSE_ITEMS.map((item) => {
          const copy = t.workspace.home.courseItems[item.id];
          return (
            <button
              key={item.id}
              type="button"
              data-course-entry={item.id}
              aria-label={`${copy.title}：${copy.description}`}
              onClick={() => onSelect(item)}
              onMouseEnter={() => queuePreview(item.id)}
              onMouseLeave={hidePreview}
              onFocus={() => showPreview(item.id)}
              onBlur={hidePreview}
              className={cn(
                "group bg-background flex h-full min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-200/80 text-left shadow-sm",
                previewAllowed &&
                  "transition-[transform,box-shadow,border-color] duration-200 hover:-translate-y-1 hover:border-indigo-200 hover:shadow-xl",
              )}
            >
              <CourseMedia
                item={item}
                preview={activePreviewId === item.id}
                failed={failedMedia.has(item.id)}
                onError={() =>
                  setFailedMedia((current) => new Set(current).add(item.id))
                }
              />
              <span className="flex flex-1 flex-col p-4 sm:p-5">
                <span className="flex items-center justify-between gap-3">
                  <span className="min-w-0 text-base font-semibold">
                    {copy.title}
                  </span>
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
        })}
      </div>
    </section>
  );
}
