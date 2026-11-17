"use client";

import {
  ChevronRight,
  Download,
  FileCheck2,
  ImageOff,
  Maximize2,
  Play,
  Plus,
  Presentation,
} from "lucide-react";
import { useState } from "react";

import type {
  DeckCase,
  LocalizedText,
  ShowcaseCase,
  VideoCase,
  VisualCase,
} from "@/core/agents/showcase";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import {
  formatBytes,
  formatDuration,
  localized,
  type ShowcaseLocale,
} from "./showcase-utils";

export function PromptButton({
  item,
  title,
  locale,
  disabled,
  onUsePrompt,
}: {
  item: ShowcaseCase;
  title: LocalizedText;
  locale: ShowcaseLocale;
  disabled: boolean;
  onUsePrompt: (prompt: string) => void;
}) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      data-showcase-prompt
      data-testid={`showcase-prompt-${item.id}`}
      aria-label={`${t.agents.showcase.usePrompt}: ${localized(title, locale)}`}
      disabled={disabled}
      onClick={() => onUsePrompt(item.prompt)}
      className="showcase-prompt border-border bg-card text-muted-foreground inline-flex min-h-9 items-center justify-center gap-1.5 rounded-md border px-3 text-xs font-medium transition focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/10"
    >
      <Plus className="h-3.5 w-3.5" aria-hidden="true" />
      {t.agents.showcase.usePrompt}
    </button>
  );
}

export function VideoCard({
  item,
  locale,
  eager,
  disabled,
  onOpen,
  onUsePrompt,
}: {
  item: VideoCase;
  locale: ShowcaseLocale;
  eager: boolean;
  disabled: boolean;
  onOpen: (item: VideoCase, trigger: HTMLButtonElement) => void;
  onUsePrompt: (prompt: string) => void;
}) {
  const { t } = useI18n();
  const [failed, setFailed] = useState(false);
  const title = localized(item.title, locale);

  return (
    <article
      data-testid={`showcase-case-${item.id}`}
      className="showcase-card border-border flex min-w-0 flex-col overflow-hidden rounded-[15px] border transition duration-200 hover:-translate-y-0.5 hover:border-indigo-300 hover:shadow-md motion-reduce:hover:translate-y-0 dark:border-white/10 dark:hover:border-indigo-400/50"
    >
      <button
        type="button"
        data-testid={`showcase-open-${item.id}`}
        aria-label={`${t.agents.showcase.play} ${title}`}
        onClick={(event) => onOpen(item, event.currentTarget)}
        className="group relative block aspect-video w-full overflow-hidden bg-neutral-950 text-left focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none"
      >
        {failed ? (
          <span className="text-muted-foreground flex h-full w-full flex-col items-center justify-center gap-2 bg-slate-100 text-xs dark:bg-white/5">
            <ImageOff className="h-5 w-5" aria-hidden="true" />
            {t.agents.showcase.mediaUnavailable}
          </span>
        ) : (
          <img
            src={item.poster}
            alt={title}
            width={1280}
            height={720}
            loading={eager ? "eager" : "lazy"}
            fetchPriority={eager ? "high" : "auto"}
            decoding="async"
            onError={() => setFailed(true)}
            className="h-full w-full object-cover transition duration-200 group-hover:scale-[1.02] motion-reduce:group-hover:scale-100"
          />
        )}
        <span className="absolute right-3 bottom-3 rounded-full bg-black/70 px-2 py-1 text-[10px] font-medium text-white">
          {formatDuration(item.durationSeconds)}
        </span>
        <span className="absolute top-1/2 left-1/2 flex h-11 w-11 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-indigo-700 shadow-lg transition group-hover:scale-105 motion-reduce:group-hover:scale-100">
          <Play
            className="ml-0.5 h-5 w-5"
            fill="currentColor"
            aria-hidden="true"
          />
        </span>
      </button>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <span className="showcase-tag inline-flex self-start rounded px-2 py-1 text-[10px] font-medium">
          {localized(item.tag, locale)}
        </span>
        <h3 className="text-foreground text-[15px] leading-6 font-semibold">
          {title}
        </h3>
        <p className="text-muted-foreground line-clamp-3 text-xs leading-5">
          {localized(item.description, locale)}
        </p>
        <div className="border-border mt-auto flex items-end justify-between gap-3 border-t pt-3">
          <div className="text-muted-foreground min-w-0 text-[10px] leading-5">
            <span className="block truncate">
              {localized(item.voice, locale)}
            </span>
            <span className="block truncate">
              {localized(item.styleLabel, locale)}
            </span>
          </div>
          <PromptButton
            item={item}
            title={item.title}
            locale={locale}
            disabled={disabled}
            onUsePrompt={onUsePrompt}
          />
        </div>
      </div>
    </article>
  );
}

export function DeckCard({
  item,
  locale,
  eager,
  disabled,
  onOpen,
  onUsePrompt,
}: {
  item: DeckCase;
  locale: ShowcaseLocale;
  eager: boolean;
  disabled: boolean;
  onOpen: (
    item: DeckCase,
    trigger: HTMLButtonElement,
    slideIndex?: number,
  ) => void;
  onUsePrompt: (prompt: string) => void;
}) {
  const { t } = useI18n();
  const [failed, setFailed] = useState(false);
  const title = localized(item.title, locale);
  const previewCount = Math.min(4, item.slides.length);
  const remainingCount = Math.max(0, item.pageCount - previewCount);

  return (
    <article
      data-testid={`showcase-case-${item.id}`}
      className="showcase-card border-border grid min-w-0 overflow-hidden rounded-[15px] border transition duration-200 hover:border-amber-300 hover:shadow-md md:grid-cols-[240px_minmax(0,1fr)] xl:grid-cols-[286px_minmax(0,1fr)] dark:border-white/10 dark:hover:border-amber-400/50"
    >
      <div className="relative grid grid-cols-2 content-start gap-2 p-4 md:p-5">
        {failed ? (
          <span className="text-muted-foreground col-span-2 flex aspect-video flex-col items-center justify-center gap-2 rounded-lg bg-slate-100 text-xs dark:bg-white/5">
            <ImageOff className="h-5 w-5" aria-hidden="true" />
            {t.agents.showcase.mediaUnavailable}
          </span>
        ) : (
          item.slides.slice(0, previewCount).map((slide, slideIndex) => (
            <button
              key={slide}
              type="button"
              data-testid={`showcase-open-${item.id}${slideIndex === 0 ? "" : `-${slideIndex}`}`}
              aria-label={`${t.agents.showcase.openCase}: ${title} · ${slideIndex + 1} / ${item.pageCount}`}
              onClick={(event) => onOpen(item, event.currentTarget, slideIndex)}
              className="group border-border relative block aspect-video overflow-hidden rounded-lg border bg-neutral-950 text-left focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none"
            >
              <img
                src={slide}
                alt={`${title} · ${slideIndex + 1} / ${item.pageCount}`}
                width={1280}
                height={720}
                loading={eager && slideIndex === 0 ? "eager" : "lazy"}
                fetchPriority={eager && slideIndex === 0 ? "high" : "auto"}
                decoding="async"
                onError={() => setFailed(true)}
                className="h-full w-full object-cover transition duration-200 group-hover:scale-[1.03] motion-reduce:group-hover:scale-100"
              />
              <span className="absolute right-1 bottom-1 rounded bg-black/65 px-1.5 py-0.5 text-[9px] text-white tabular-nums">
                {String(slideIndex + 1).padStart(2, "0")}
              </span>
            </button>
          ))
        )}
        {remainingCount > 0 ? (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute right-6 bottom-6 inline-flex items-center gap-1 rounded bg-black/70 px-2 py-1 text-[10px] text-white"
          >
            <Presentation className="h-3 w-3" aria-hidden="true" />+
            {remainingCount} {t.agents.showcase.slides}
          </span>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col gap-2 p-5 pt-0 md:pt-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="showcase-tag rounded px-2 py-1 text-[10px] font-medium">
            {localized(item.tag, locale)}
          </span>
          <span className="text-muted-foreground text-[10px]">
            {localized(item.styleLabel, locale)}
          </span>
        </div>
        <h3 className="text-foreground text-lg leading-7 font-bold tracking-tight">
          {title}
        </h3>
        <p className="text-muted-foreground line-clamp-3 text-xs leading-5">
          {localized(item.description, locale)}
        </p>
        <div className="flex flex-wrap gap-1.5 pt-1">
          {item.features.map((feature) => (
            <span
              key={feature.en}
              className="text-muted-foreground border-border inline-flex items-center gap-1 rounded border px-1.5 py-1 text-[10px]"
            >
              <FileCheck2
                className="h-3 w-3 text-amber-600 dark:text-amber-300"
                aria-hidden="true"
              />
              {localized(feature, locale)}
            </span>
          ))}
        </div>
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-3">
          <button
            type="button"
            data-testid={`showcase-open-all-${item.id}`}
            onClick={(event) => onOpen(item, event.currentTarget, 0)}
            className="inline-flex min-h-9 items-center gap-1 rounded-md bg-neutral-900 px-3 text-xs font-medium text-white hover:bg-neutral-700 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none dark:bg-neutral-100 dark:text-neutral-900"
          >
            {t.agents.showcase.openCase}
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <a
            data-testid={`showcase-card-download-${item.id}`}
            href={item.pptx}
            download={`${item.id}.pptx`}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-3 text-xs font-medium text-amber-700 hover:bg-amber-100 focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none dark:border-amber-400/20 dark:bg-amber-400/10 dark:text-amber-300"
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            {t.agents.showcase.downloadPptx}
          </a>
          <PromptButton
            item={item}
            title={item.title}
            locale={locale}
            disabled={disabled}
            onUsePrompt={onUsePrompt}
          />
        </div>
        <span className="text-muted-foreground pt-1 text-[10px]">
          {item.pageCount} {t.agents.showcase.slides} ·{" "}
          {formatBytes(item.pptxBytes, locale)}
        </span>
      </div>
    </article>
  );
}

export function VisualCard({
  item,
  locale,
  eager,
  disabled,
  onOpen,
  onUsePrompt,
}: {
  item: VisualCase;
  locale: ShowcaseLocale;
  eager: boolean;
  disabled: boolean;
  onOpen: (item: VisualCase, trigger: HTMLButtonElement) => void;
  onUsePrompt: (prompt: string) => void;
}) {
  const { t } = useI18n();
  const [failed, setFailed] = useState(false);
  const title = localized(item.title, locale);

  return (
    <article
      data-testid={`showcase-case-${item.id}`}
      className="showcase-card border-border mb-4 inline-flex w-full min-w-0 break-inside-avoid flex-col overflow-hidden rounded-[13px] border transition duration-200 hover:border-rose-300 hover:shadow-md dark:border-white/10 dark:hover:border-rose-400/50"
    >
      <button
        type="button"
        data-testid={`showcase-open-${item.id}`}
        aria-label={`${t.agents.showcase.openCase}: ${title}`}
        onClick={(event) => onOpen(item, event.currentTarget)}
        style={{ aspectRatio: `${item.width} / ${item.height}` }}
        className="group relative block w-full overflow-hidden bg-neutral-950 text-left focus-visible:ring-2 focus-visible:ring-rose-500 focus-visible:outline-none"
      >
        {failed ? (
          <span className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 bg-slate-100 text-xs dark:bg-white/5">
            <ImageOff className="h-5 w-5" aria-hidden="true" />
            {t.agents.showcase.mediaUnavailable}
          </span>
        ) : (
          <img
            src={item.preview}
            alt={title}
            width={item.width}
            height={item.height}
            loading={eager ? "eager" : "lazy"}
            fetchPriority={eager ? "high" : "auto"}
            decoding="async"
            onError={() => setFailed(true)}
            className="h-auto w-full object-contain transition duration-200 group-hover:scale-[1.01] motion-reduce:group-hover:scale-100"
          />
        )}
        <span className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-1 bg-black/60 py-2 text-[10px] text-white opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100">
          <Maximize2 className="h-3 w-3" aria-hidden="true" />
          {t.agents.showcase.openCase}
        </span>
        {item.vector ? (
          <span className="absolute top-3 right-3 rounded-full bg-black/70 px-2 py-1 text-[10px] font-medium text-white">
            SVG
          </span>
        ) : null}
      </button>
      <div className="flex flex-1 flex-col gap-1.5 p-3">
        <span className="showcase-tag inline-flex self-start rounded px-2 py-1 text-[10px] font-medium">
          {localized(item.tag, locale)}
        </span>
        <h3 className="text-foreground text-[13px] leading-5 font-semibold">
          {title}
        </h3>
        <div className="text-muted-foreground text-[10px]">
          {item.width} × {item.height} · {localized(item.styleLabel, locale)}
        </div>
        <div className="mt-auto pt-1 [&_[data-showcase-prompt]]:w-full">
          <PromptButton
            item={item}
            title={item.title}
            locale={locale}
            disabled={disabled}
            onUsePrompt={onUsePrompt}
          />
        </div>
      </div>
    </article>
  );
}

export function showcaseCardClass(kind: "video" | "visual" | "deck") {
  return cn(
    kind === "deck" && "flex flex-col gap-4",
    kind === "video" && "grid gap-4 sm:grid-cols-2 xl:grid-cols-3",
    kind === "visual" && "columns-1 gap-4 sm:columns-2 xl:columns-3",
  );
}
