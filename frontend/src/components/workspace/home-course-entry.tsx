"use client";

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
}: {
  item: HomeCourseItem;
  failed: boolean;
  videoRef?: (element: HTMLVideoElement | null) => void;
  onError: () => void;
}) {
  const { t } = useI18n();
  const copy = t.workspace.home.courseItems[item.id];
  if (item.media.kind === "video") {
    return (
      <div className="bg-muted relative aspect-video overflow-hidden">
        <video
          ref={videoRef}
          className="h-full w-full object-cover"
          src={item.media.src}
          muted
          loop
          playsInline
          preload="auto"
          aria-label={copy.mediaAlt}
          onError={onError}
        />
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
  if (item.media.kind === "image") {
    return (
      <div className="bg-muted aspect-video overflow-hidden">
        <img
          src={item.media.src}
          alt={copy.mediaAlt}
          className="h-full w-full object-cover"
          loading="lazy"
        />
      </div>
    );
  }
  if (item.media.kind === "ppt") {
    return (
      <div
        className="to-background relative aspect-video overflow-hidden bg-gradient-to-br from-indigo-50 p-8"
        role="img"
        aria-label={copy.mediaAlt}
      >
        <div className="bg-background flex h-full flex-col rounded-lg border p-5 shadow-sm">
          <span className="h-2 w-16 rounded-full bg-indigo-400" />
          <span className="mt-5 text-xl font-bold">Course slides</span>
          <span className="mt-3 h-2 w-4/5 rounded bg-slate-200" />
          <span className="mt-2 h-2 w-3/5 rounded bg-slate-200" />
          <div className="mt-auto flex gap-2">
            <span className="h-10 flex-1 rounded bg-indigo-100" />
            <span className="h-10 flex-1 rounded bg-pink-100" />
            <span className="h-10 flex-1 rounded bg-teal-100" />
          </div>
        </div>
      </div>
    );
  }
  return (
    <div
      className="to-background aspect-video bg-gradient-to-br from-amber-50 p-8"
      role="img"
      aria-label={copy.mediaAlt}
    >
      <div className="bg-background h-full rounded-lg border p-5 shadow-sm">
        <span className="block text-xs font-semibold tracking-widest text-amber-600 uppercase">
          Article
        </span>
        <span className="mt-4 block text-lg font-bold">A clear story</span>
        <span className="mt-4 block h-2 w-full rounded bg-slate-200" />
        <span className="mt-2 block h-2 w-11/12 rounded bg-slate-200" />
        <span className="mt-2 block h-2 w-4/5 rounded bg-slate-200" />
        <span className="mt-5 block h-2 w-2/3 rounded bg-slate-100" />
      </div>
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
      video.play().catch(() => {
        const id = video.dataset.courseItem;
        if (id) setFailedMedia((current) => new Set(current).add(id));
      });
    }
    return () => videos.forEach((video) => video.pause());
  }, [activeEntry, reducedMotion, pageVisible]);

  const entries: { id: CourseEntryId; label: string; description: string }[] = [
    {
      id: "video",
      label: t.workspace.home.videoCourse,
      description: t.workspace.home.videoCourseDescription,
    },
    {
      id: "text",
      label: t.workspace.home.textCourse,
      description: t.workspace.home.textCourseDescription,
    },
  ];
  const items = HOME_COURSE_ITEMS.filter((item) => item.entry === activeEntry);

  return (
    <section aria-label={t.workspace.home.createHeading}>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {entries.map((entry) => {
          const active = entry.id === activeEntry;
          return (
            <button
              key={entry.id}
              id={`home-course-tab-${entry.id}`}
              type="button"
              aria-expanded={active}
              aria-controls={`home-course-panel-${entry.id}`}
              onClick={() => setActiveEntry(entry.id)}
              className={cn(
                "rounded-2xl border p-5 text-left transition-colors",
                active
                  ? "border-indigo-400 bg-indigo-50/70"
                  : "bg-background/70 hover:border-indigo-200",
              )}
            >
              <span className="block text-lg font-semibold">{entry.label}</span>
              <span className="text-muted-foreground mt-1 block text-sm">
                {entry.description}
              </span>
            </button>
          );
        })}
      </div>
      <div
        id={`home-course-panel-${activeEntry}`}
        role="region"
        aria-labelledby={`home-course-tab-${activeEntry}`}
        className="mt-5 grid grid-cols-1 gap-5 md:grid-cols-3"
      >
        {items.map((item) => {
          const copy = t.workspace.home.courseItems[item.id];
          return (
            <button
              key={item.id}
              type="button"
              data-course-entry={item.id}
              onClick={() => onSelect(item)}
              className="bg-background group overflow-hidden rounded-2xl border text-left shadow-sm transition-shadow hover:shadow-md"
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
              />
              <span className="block p-5">
                <span className="flex items-center justify-between gap-3">
                  <span className="text-base font-semibold">{copy.title}</span>
                  <span className="text-muted-foreground rounded-full bg-slate-100 px-2 py-1 text-xs">
                    {copy.badge}
                  </span>
                </span>
                <span className="text-muted-foreground mt-2 block text-sm leading-6">
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
