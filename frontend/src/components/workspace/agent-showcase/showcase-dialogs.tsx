"use client";

import {
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  ImageOff,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type {
  DeckCase,
  PodcastCase,
  ShowcaseSource,
  VideoCase,
  VisualCase,
} from "@/core/agents/showcase";
import { useI18n } from "@/core/i18n/hooks";

import {
  formatBytes,
  formatDuration,
  localized,
  type ShowcaseLocale,
} from "./showcase-utils";

export function SourceLinks({
  sources,
  locale,
  testIdPrefix,
}: {
  sources?: ShowcaseSource[];
  locale: ShowcaseLocale;
  testIdPrefix?: string;
}) {
  const { t } = useI18n();
  if (!sources?.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted-foreground font-medium">
        {t.agents.showcase.sources}
      </span>
      {sources.map((source, index) => (
        <a
          key={source.url}
          data-testid={testIdPrefix ? `${testIdPrefix}-${index}` : undefined}
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
            {localized(item.tag, locale)} ·{" "}
            {formatDuration(item.durationSeconds)}
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

export function DeckDialog({
  item,
  initialSlide,
  locale,
  onClose,
}: {
  item: DeckCase;
  initialSlide: number;
  locale: ShowcaseLocale;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [index, setIndex] = useState(initialSlide);
  const title = localized(item.title, locale);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        showCloseButton={false}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") {
            event.preventDefault();
            setIndex((current) => Math.max(0, current - 1));
          }
          if (event.key === "ArrowRight") {
            event.preventDefault();
            setIndex((current) =>
              Math.min(item.slides.length - 1, current + 1),
            );
          }
        }}
        className="max-h-[94vh] overflow-y-auto rounded-2xl p-4 sm:max-w-5xl sm:p-6 dark:border-white/10 dark:bg-[#0b1020]"
      >
        <DialogHeader className="pr-10 text-left">
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {localized(item.tag, locale)} · {item.pageCount}{" "}
            {t.agents.showcase.slides}
          </DialogDescription>
        </DialogHeader>
        <DialogClose asChild>
          <button
            type="button"
            data-testid="showcase-dialog-close"
            aria-label={t.agents.showcase.close}
            className="text-muted-foreground hover:bg-accent hover:text-foreground absolute top-4 right-4 rounded-full p-2 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </DialogClose>
        <div className="relative overflow-hidden rounded-xl border border-white/10 bg-neutral-950">
          <img
            data-testid="showcase-deck-current"
            src={item.slides[index]}
            alt={`${title} · ${index + 1} / ${item.pageCount}`}
            width={1280}
            height={720}
            decoding="async"
            className="aspect-video h-full max-h-[62vh] w-full object-contain"
          />
          <span className="absolute right-3 bottom-3 rounded-full bg-black/70 px-2.5 py-1 text-xs text-white tabular-nums">
            {index + 1} / {item.pageCount}
          </span>
        </div>
        <div className="flex items-center justify-between gap-3">
          <button
            type="button"
            data-testid="showcase-deck-previous"
            disabled={index === 0}
            onClick={() => setIndex((current) => Math.max(0, current - 1))}
            className="border-border inline-flex min-h-9 items-center gap-1 rounded-full border px-3 text-xs disabled:opacity-40 dark:border-white/10"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            {t.agents.showcase.previous}
          </button>
          <button
            type="button"
            data-testid="showcase-deck-next"
            disabled={index === item.slides.length - 1}
            onClick={() =>
              setIndex((current) =>
                Math.min(item.slides.length - 1, current + 1),
              )
            }
            className="border-border inline-flex min-h-9 items-center gap-1 rounded-full border px-3 text-xs disabled:opacity-40 dark:border-white/10"
          >
            {t.agents.showcase.next}
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <div className="grid grid-cols-7 gap-2">
          {item.slides.map((slide, slideIndex) => (
            <button
              key={slide}
              type="button"
              data-testid={`showcase-deck-thumbnail-${slideIndex}`}
              aria-label={`${slideIndex + 1} / ${item.pageCount}`}
              aria-pressed={slideIndex === index}
              onClick={() => setIndex(slideIndex)}
              className={`overflow-hidden rounded-md border-2 transition ${slideIndex === index ? "border-amber-500" : "border-transparent opacity-65 hover:opacity-100"}`}
            >
              <img
                src={slide}
                alt=""
                width={1280}
                height={720}
                loading="lazy"
                decoding="async"
                className="aspect-video w-full object-cover"
              />
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-muted-foreground text-xs">
            {localized(item.styleLabel, locale)} ·{" "}
            {formatBytes(item.pptxBytes, locale)}
          </span>
          <a
            data-testid="showcase-download-pptx"
            href={item.pptx}
            download={`${item.id}.pptx`}
            className="inline-flex min-h-9 items-center gap-2 rounded-full bg-amber-700 px-4 text-xs font-medium text-white hover:bg-amber-800 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none"
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            {t.agents.showcase.downloadPptx}
          </a>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function VisualDialog({
  item,
  locale,
  onClose,
}: {
  item: VisualCase;
  locale: ShowcaseLocale;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [failed, setFailed] = useState(false);
  const title = localized(item.title, locale);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        showCloseButton={false}
        className="max-h-[94vh] overflow-y-auto rounded-2xl p-4 sm:max-w-5xl sm:p-6 dark:border-white/10 dark:bg-[#0b1020]"
      >
        <DialogHeader className="pr-10 text-left">
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {localized(item.tag, locale)} · {item.width} × {item.height}
          </DialogDescription>
        </DialogHeader>
        <DialogClose asChild>
          <button
            type="button"
            data-testid="showcase-dialog-close"
            aria-label={t.agents.showcase.close}
            className="text-muted-foreground hover:bg-accent hover:text-foreground absolute top-4 right-4 rounded-full p-2 focus-visible:ring-2 focus-visible:ring-fuchsia-500 focus-visible:outline-none"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </DialogClose>
        <div className="flex min-h-40 items-center justify-center overflow-hidden rounded-xl border border-white/10 bg-neutral-950">
          {failed ? (
            <span className="text-muted-foreground flex flex-col items-center gap-2 p-8 text-xs">
              <ImageOff className="h-5 w-5" aria-hidden="true" />
              {t.agents.showcase.mediaUnavailable}
            </span>
          ) : (
            <img
              data-testid="showcase-visual-original"
              src={item.original}
              alt={title}
              width={item.width}
              height={item.height}
              decoding="async"
              onError={() => setFailed(true)}
              className="max-h-[70vh] w-full object-contain"
            />
          )}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-muted-foreground text-xs">
            {localized(item.styleLabel, locale)} · {item.width} × {item.height}
          </span>
          <a
            data-testid={
              item.vector
                ? "showcase-download-svg"
                : "showcase-download-original"
            }
            href={item.vector ?? item.original}
            download={item.downloadName}
            className="inline-flex min-h-9 items-center gap-2 rounded-full bg-fuchsia-600 px-4 text-xs font-medium text-white hover:bg-fuchsia-500 focus-visible:ring-2 focus-visible:ring-fuchsia-500 focus-visible:outline-none"
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            {item.vector
              ? t.agents.showcase.downloadSvg
              : t.agents.showcase.downloadOriginal}
          </a>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function PodcastDialog({
  item,
  locale,
  onClose,
}: {
  item: PodcastCase;
  locale: ShowcaseLocale;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        showCloseButton={false}
        className="max-h-[94vh] overflow-y-auto rounded-2xl p-4 sm:max-w-3xl sm:p-6 dark:border-white/10 dark:bg-[#0b1020]"
      >
        <DialogHeader className="pr-10 text-left">
          <DialogTitle>{localized(item.title, locale)}</DialogTitle>
          <DialogDescription>
            {localized(item.tag, locale)} ·{" "}
            {formatDuration(item.durationSeconds)} · {item.transcript.length}
          </DialogDescription>
        </DialogHeader>
        <DialogClose asChild>
          <button
            type="button"
            data-testid="showcase-dialog-close"
            aria-label={t.agents.showcase.close}
            className="text-muted-foreground hover:bg-accent hover:text-foreground absolute top-4 right-4 rounded-full p-2 focus-visible:ring-2 focus-visible:ring-cyan-500 focus-visible:outline-none"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </DialogClose>
        <SourceLinks
          sources={item.sources}
          locale={locale}
          testIdPrefix="showcase-podcast-source"
        />
        <div className="grid gap-4">
          {item.transcript.map((turn, index) => (
            <div
              key={`${turn.speaker.en}-${index}`}
              className="grid gap-1 border-l-2 border-cyan-200 pl-3 dark:border-cyan-400/30"
            >
              <span className="text-xs font-semibold text-cyan-700 dark:text-cyan-300">
                {localized(turn.speaker, locale)}
              </span>
              <p className="text-foreground text-sm leading-7">{turn.text}</p>
            </div>
          ))}
        </div>
        <div className="flex justify-end">
          <a
            data-testid="showcase-download-mp3"
            href={item.audio}
            download={`${item.id}.mp3`}
            className="inline-flex min-h-9 items-center gap-2 rounded-full bg-cyan-600 px-4 text-xs font-medium text-white hover:bg-cyan-500 focus-visible:ring-2 focus-visible:ring-cyan-500 focus-visible:outline-none"
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            {t.agents.showcase.downloadMp3}
          </a>
        </div>
      </DialogContent>
    </Dialog>
  );
}
