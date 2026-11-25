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
  promptActionLabel,
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
  const label = promptActionLabel(item, locale, t.agents.showcase.usePrompt);
  return (
    <button
      type="button"
      data-showcase-prompt
      data-testid={`showcase-prompt-${item.id}`}
      aria-label={`${label}: ${localized(title, locale)}`}
      disabled={disabled}
      onClick={() => onUsePrompt(item.prompt)}
      className="showcase-prompt border-border bg-card text-muted-foreground inline-flex min-h-9 shrink-0 items-center justify-center gap-1.5 rounded-md border px-3 text-xs font-medium whitespace-nowrap transition focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/10"
    >
      <Plus className="h-3.5 w-3.5" aria-hidden="true" />
      {label}
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
      className="showcase-card flex min-w-0 flex-col overflow-hidden rounded-[16px] transition duration-200 hover:-translate-y-0.5 motion-reduce:hover:translate-y-0"
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
        <span className="showcase-tag inline-flex self-start rounded px-2 py-1 text-[11px] font-medium">
          {localized(item.tag, locale)}
        </span>
        <h3 className="text-foreground text-[18px] leading-7 font-semibold">
          {title}
        </h3>
        <p className="text-muted-foreground line-clamp-3 text-[13px] leading-[1.7]">
          {localized(item.description, locale)}
        </p>
        <div className="border-border mt-auto flex items-end justify-between gap-3 border-t pt-3">
          <div className="text-muted-foreground min-w-0 text-[10.5px] leading-5">
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
  const [failedSlides, setFailedSlides] = useState<Set<number>>(new Set());
  const title = localized(item.title, locale);
  const previewCount = Math.min(4, item.slides.length);
  const remainingCount = Math.max(0, item.pageCount - previewCount);

  return (
    <article
      data-testid={`showcase-case-${item.id}`}
      className="showcase-card grid min-w-0 overflow-hidden rounded-[16px] transition duration-200 md:grid-cols-[250px_minmax(0,1fr)] xl:grid-cols-[296px_minmax(0,1fr)]"
    >
      <div className="relative grid grid-cols-2 content-start gap-2 p-4 md:p-5">
        {item.slides.slice(0, previewCount).map((slide, slideIndex) =>
          failedSlides.has(slideIndex) ? (
            <span
              key={slide}
              className="text-muted-foreground flex aspect-video flex-col items-center justify-center gap-1 rounded-lg bg-slate-100 p-2 text-center text-[10px] dark:bg-white/5"
            >
              <ImageOff className="h-4 w-4" aria-hidden="true" />
              {t.agents.showcase.mediaUnavailable}
            </span>
          ) : (
            <button
              key={slide}
              type="button"
              data-testid={`showcase-open-${item.id}${slideIndex === 0 ? "" : `-${slideIndex}`}`}
              aria-label={`${t.agents.showcase.openCase}: ${title} · ${slideIndex + 1} / ${item.pageCount}`}
              onClick={(event) => onOpen(item, event.currentTarget, slideIndex)}
              className="group border-border/60 relative block aspect-video overflow-hidden rounded-[10px] border bg-neutral-950 text-left focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none"
            >
              <img
                src={slide}
                alt={`${title} · ${slideIndex + 1} / ${item.pageCount}`}
                width={1280}
                height={720}
                loading={eager && slideIndex === 0 ? "eager" : "lazy"}
                fetchPriority={eager && slideIndex === 0 ? "high" : "auto"}
                decoding="async"
                onError={() =>
                  setFailedSlides((current) => new Set(current).add(slideIndex))
                }
                className="h-full w-full object-cover transition duration-200 group-hover:scale-[1.03] motion-reduce:group-hover:scale-100"
              />
              <span className="absolute right-1 bottom-1 rounded bg-black/65 px-1.5 py-0.5 text-[9px] text-white tabular-nums">
                {String(slideIndex + 1).padStart(2, "0")}
              </span>
            </button>
          ),
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
          <span className="showcase-tag rounded px-2 py-1 text-[11px] font-medium">
            {localized(item.tag, locale)}
          </span>
          <span className="text-muted-foreground text-[10.5px]">
            {t.agents.showcase.stylePrefix}
            {localized(item.styleLabel, locale)}
          </span>
        </div>
        <h3 className="text-foreground text-[19px] leading-[1.4] font-bold tracking-tight">
          {title}
        </h3>
        <p className="text-muted-foreground line-clamp-3 text-[13px] leading-6">
          {localized(item.description, locale)}
        </p>
        <div className="flex flex-wrap gap-1.5 pt-1">
          {item.features.map((feature) => (
            <span
              key={feature.en}
              className="text-muted-foreground border-border/70 inline-flex items-center gap-1 rounded border px-1.5 py-1 text-[11px]"
            >
              <FileCheck2
                className="h-3 w-3 text-indigo-600 dark:text-indigo-300"
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
            className="inline-flex min-h-9 items-center gap-1 rounded-md bg-neutral-900 px-3 text-xs font-medium whitespace-nowrap text-white hover:bg-neutral-700 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none dark:bg-neutral-100 dark:text-neutral-900"
          >
            {t.agents.showcase.openCase}
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <a
            data-testid={`showcase-card-download-${item.id}`}
            href={item.pptx}
            download={`${item.id}.pptx`}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-indigo-200 bg-indigo-50 px-3 text-xs font-medium whitespace-nowrap text-indigo-700 hover:bg-indigo-100 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none dark:border-indigo-400/20 dark:bg-indigo-400/10 dark:text-indigo-300"
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
        <span className="text-muted-foreground pt-1 text-[10.5px]">
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
      className="showcase-card mb-4 inline-flex w-full min-w-0 break-inside-avoid flex-col overflow-hidden rounded-[16px] transition duration-200"
    >
      <button
        type="button"
        data-testid={`showcase-open-${item.id}`}
        aria-label={`${t.agents.showcase.openCase}: ${title}`}
        onClick={(event) => onOpen(item, event.currentTarget)}
        style={{ aspectRatio: `${item.width} / ${item.height}` }}
        className="group relative block w-full overflow-hidden bg-neutral-950 text-left focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none"
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
            {t.agents.showcase.svgEditable}
          </span>
        ) : null}
      </button>
      <div className="flex flex-1 flex-col gap-1.5 p-3">
        <span className="showcase-tag inline-flex self-start rounded px-2 py-1 text-[11px] font-medium">
          {localized(item.tag, locale)}
        </span>
        <h3 className="text-foreground text-[15px] leading-6 font-semibold">
          {title}
        </h3>
        <p className="text-muted-foreground line-clamp-2 text-[13px] leading-5">
          {localized(item.description, locale)}
        </p>
        <div className="text-muted-foreground text-[10.5px]">
          {item.width} × {item.height} · {localized(item.formatLabel, locale)}
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
