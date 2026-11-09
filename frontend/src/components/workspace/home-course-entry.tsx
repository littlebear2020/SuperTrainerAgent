"use client";

import { ArrowRight, BookOpenText, Video } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import {
  type CourseEntryId,
  HOME_COURSE_ITEMS,
  type HomeCourseItem,
} from "./home-course-catalog";

function CourseMedia({
  item,
  failed,
  videoRef,
  onError,
  onPlaying,
}: {
  item: HomeCourseItem;
  failed: boolean;
  videoRef?: (element: HTMLVideoElement | null) => void;
  onError: () => void;
  onPlaying?: () => void;
}) {
  const { t } = useI18n();
  const copy = t.workspace.home.courseItems[item.id];
  return (
    <div className="bg-muted relative aspect-video overflow-hidden">
      {item.media.kind === "video" ? (
        <video
          ref={videoRef}
          className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.018]"
          src={item.media.src}
          muted
          loop
          playsInline
          preload="metadata"
          aria-label={copy.mediaAlt}
          onError={onError}
          onPlaying={onPlaying}
        />
      ) : (
        <img
          src={item.media.src}
          alt={copy.mediaAlt}
          className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.018]"
          loading="lazy"
        />
      )}
      <span className="absolute top-3 left-3 rounded-full border border-white/25 bg-slate-950/35 px-2.5 py-1 text-[11px] font-medium whitespace-nowrap text-white backdrop-blur-sm">
        {copy.badge}
      </span>
      {failed ? (
        <div
          role="status"
          className="bg-background/90 pointer-events-none absolute inset-0 flex items-center justify-center p-4 text-sm"
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
  const [activeEntry, setActiveEntry] = useState<CourseEntryId>("video");
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [pageVisible, setPageVisible] = useState(() => !document.hidden);
  const [failedMedia, setFailedMedia] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const videoRefs = useRef(new Map<string, HTMLVideoElement>());

  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handlePreference = () => setReducedMotion(preference.matches);
    const handleVisibility = () => setPageVisible(!document.hidden);
    preference.addEventListener("change", handlePreference);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      preference.removeEventListener("change", handlePreference);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);

  useEffect(() => {
    const videos = [...videoRefs.current.values()];
    if (reducedMotion || !pageVisible) {
      videos.forEach((video) => video.pause());
      return;
    }
    for (const video of videos) {
      video.play().catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        const id = video.dataset.courseItem;
        if (id) setFailedMedia((current) => new Set(current).add(id));
      });
    }
    return () => videos.forEach((video) => video.pause());
  }, [activeEntry, reducedMotion, pageVisible]);

  const entries = [
    {
      id: "video" as const,
      label: t.workspace.home.videoCourse,
      description: t.workspace.home.videoCourseDescription,
      icon: Video,
    },
    {
      id: "text" as const,
      label: t.workspace.home.textCourse,
      description: t.workspace.home.textCourseDescription,
      icon: BookOpenText,
    },
  ];
  const items = HOME_COURSE_ITEMS.filter((item) => item.entry === activeEntry);

  return (
    <section aria-label={t.workspace.home.createHeading}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {entries.map((entry) => {
          const active = entry.id === activeEntry;
          const Icon = entry.icon;
          return (
            <button
              key={entry.id}
              id={`home-course-tab-${entry.id}`}
              type="button"
              aria-expanded={active}
              aria-controls={`home-course-panel-${entry.id}`}
              onClick={() => setActiveEntry(entry.id)}
              className={cn(
                "group relative min-h-32 overflow-hidden rounded-2xl border p-5 text-left shadow-sm transition-[transform,box-shadow,border-color] duration-200 hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-lg sm:p-6",
                active
                  ? "border-indigo-300 bg-gradient-to-br from-white to-indigo-50/80"
                  : "border-border/70 bg-white/80",
              )}
            >
              <span
                aria-hidden="true"
                className="pointer-events-none absolute -right-8 -bottom-16 h-36 w-36 rounded-full bg-indigo-400/10 blur-2xl"
              />
              <span className="relative flex items-center justify-between gap-4">
                <span className="flex min-w-0 items-center gap-4">
                  <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-indigo-100/80 text-indigo-600">
                    <Icon className="h-5 w-5" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-lg font-semibold whitespace-nowrap">
                      {entry.label}
                    </span>
                    <span className="text-muted-foreground mt-1 block text-sm leading-5">
                      {entry.description}
                    </span>
                  </span>
                </span>
                <ArrowRight className="text-muted-foreground h-4 w-4 shrink-0 transition-transform group-hover:translate-x-0.5 group-hover:text-indigo-600" />
              </span>
            </button>
          );
        })}
      </div>
      <div
        id={`home-course-panel-${activeEntry}`}
        role="region"
        aria-labelledby={`home-course-tab-${activeEntry}`}
        className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-3"
      >
        {items.map((item) => {
          const copy = t.workspace.home.courseItems[item.id];
          const targetLabel =
            item.kind === "agent"
              ? `${item.agentName} Agent`
              : `video-gen · ${item.initialTaskType}`;
          return (
            <button
              key={item.id}
              type="button"
              data-course-entry={item.id}
              onClick={() => onSelect(item)}
              className="group bg-background min-w-0 overflow-hidden rounded-2xl border border-slate-200/80 text-left shadow-sm transition-[transform,box-shadow,border-color] duration-200 hover:-translate-y-1 hover:border-indigo-200 hover:shadow-xl"
            >
              <CourseMedia
                item={item}
                failed={failedMedia.has(item.id)}
                videoRef={
                  item.media.kind === "video"
                    ? (element) => {
                        if (element) {
                          element.dataset.courseItem = item.id;
                          videoRefs.current.set(item.id, element);
                        } else {
                          videoRefs.current.delete(item.id);
                        }
                      }
                    : undefined
                }
                onError={() =>
                  setFailedMedia((current) => new Set(current).add(item.id))
                }
                onPlaying={() =>
                  setFailedMedia((current) => {
                    if (!current.has(item.id)) return current;
                    const next = new Set(current);
                    next.delete(item.id);
                    return next;
                  })
                }
              />
              <span className="block p-4 sm:p-5">
                <span className="flex items-center justify-between gap-3">
                  <span className="min-w-0 text-base font-semibold whitespace-nowrap">
                    {copy.title}
                  </span>
                  <span
                    data-testid="creation-arrow"
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition-colors group-hover:bg-indigo-600 group-hover:text-white"
                  >
                    <ArrowRight className="h-3.5 w-3.5" />
                  </span>
                </span>
                <span className="text-muted-foreground mt-2 block min-h-10 text-xs leading-5">
                  {copy.description}
                </span>
                <span className="text-muted-foreground mt-3 block truncate text-[11px]">
                  {targetLabel}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
