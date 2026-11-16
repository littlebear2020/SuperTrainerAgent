"use client";

import { ExternalLink, X } from "lucide-react";
import { useEffect, useRef } from "react";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ShowcaseSource, VideoCase } from "@/core/agents/showcase";
import { useI18n } from "@/core/i18n/hooks";

import { formatDuration, localized, type ShowcaseLocale } from "./showcase-utils";

export function SourceLinks({
  sources,
  locale,
}: {
  sources?: ShowcaseSource[];
  locale: ShowcaseLocale;
}) {
  const { t } = useI18n();
  if (!sources?.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted-foreground font-medium">
        {t.agents.showcase.sources}
      </span>
      {sources.map((source) => (
        <a
          key={source.url}
          href={source.url}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex items-center gap-1 rounded-full border border-indigo-200/70 px-2.5 py-1 text-indigo-700 hover:bg-indigo-50 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none dark:border-indigo-400/20 dark:text-indigo-300 dark:hover:bg-indigo-400/10"
        >
          {localized(source.label, locale)}
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
        </a>
      ))}
    </div>
  );
}

export function VideoDialog({
  item,
  locale,
  onClose,
}: {
  item: VideoCase;
  locale: ShowcaseLocale;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    return () => video?.pause();
  }, []);

  const close = () => {
    videoRef.current?.pause();
    onClose();
  };

  return (
    <Dialog open onOpenChange={(open) => !open && close()}>
      <DialogContent
        showCloseButton={false}
        className="max-h-[92vh] overflow-y-auto rounded-2xl p-4 sm:max-w-4xl sm:p-6 dark:border-white/10 dark:bg-[#0b1020]"
      >
        <DialogHeader className="pr-10 text-left">
          <DialogTitle>{localized(item.title, locale)}</DialogTitle>
          <DialogDescription>
            {localized(item.tag, locale)} · {formatDuration(item.durationSeconds)}
          </DialogDescription>
        </DialogHeader>
        <DialogClose asChild>
          <button
            type="button"
            data-testid="showcase-dialog-close"
            aria-label={t.agents.showcase.close}
            className="text-muted-foreground hover:bg-accent hover:text-foreground absolute top-4 right-4 rounded-full p-2 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </DialogClose>
        <video
          ref={videoRef}
          src={item.video}
          poster={item.poster}
          controls
          preload="metadata"
          playsInline
          className="aspect-video w-full rounded-xl border border-white/10 bg-neutral-950"
        >
          {t.agents.showcase.mediaUnavailable}
        </video>
        <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <span>{localized(item.voice, locale)}</span>
          <span>{localized(item.styleLabel, locale)}</span>
        </div>
        <SourceLinks sources={item.sources} locale={locale} />
      </DialogContent>
    </Dialog>
  );
}
