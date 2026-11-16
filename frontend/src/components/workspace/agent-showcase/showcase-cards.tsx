"use client";

import {
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
      className="inline-flex min-h-9 items-center justify-center gap-1.5 rounded-full border border-indigo-200/80 px-3 text-xs font-medium text-indigo-700 transition hover:border-indigo-400 hover:bg-indigo-50 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-45 dark:border-indigo-400/20 dark:text-indigo-300 dark:hover:bg-indigo-400/10"
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
      className="bg-card/70 border-border flex min-w-0 flex-col overflow-hidden rounded-2xl border shadow-sm backdrop-blur-sm transition duration-200 hover:-translate-y-0.5 hover:border-indigo-300 hover:shadow-md motion-reduce:hover:translate-y-0 dark:border-white/10 dark:bg-white/5 dark:hover:border-indigo-400/50"
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
        <span className="text-[11px] font-medium text-indigo-600 dark:text-indigo-300">
          {localized(item.tag, locale)}
        </span>
        <h3 className="text-foreground text-sm leading-6 font-semibold">
          {title}
        </h3>
        <p className="text-muted-foreground line-clamp-3 text-xs leading-5">
          {localized(item.description, locale)}
        </p>
        <div className="mt-auto flex items-end justify-between gap-3 pt-2">
          <div className="text-muted-foreground min-w-0 text-[11px] leading-5">
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
  onOpen: (item: DeckCase, trigger: HTMLButtonElement) => void;
  onUsePrompt: (prompt: string) => void;
}) {
  const { t } = useI18n();
  const [failed, setFailed] = useState(false);
  const title = localized(item.title, locale);

  return (
    <article
      data-testid={`showcase-case-${item.id}`}
      className="bg-card/70 border-border flex min-w-0 flex-col overflow-hidden rounded-2xl border shadow-sm backdrop-blur-sm transition duration-200 hover:-translate-y-0.5 hover:border-teal-300 hover:shadow-md motion-reduce:hover:translate-y-0 dark:border-white/10 dark:bg-white/5 dark:hover:border-teal-400/50"
    >
      <button
        type="button"
        data-testid={`showcase-open-${item.id}`}
        aria-label={`${t.agents.showcase.openCase}: ${title}`}
        onClick={(event) => onOpen(item, event.currentTarget)}
        className="group relative block aspect-video w-full overflow-hidden bg-neutral-950 text-left focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:outline-none"
      >
        {failed ? (
          <span className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 bg-slate-100 text-xs dark:bg-white/5">
            <ImageOff className="h-5 w-5" aria-hidden="true" />
            {t.agents.showcase.mediaUnavailable}
          </span>
        ) : (
          <img
            src={item.slides[0]}
            alt={`${title} · 1 / ${item.pageCount}`}
            width={1280}
            height={720}
            loading={eager ? "eager" : "lazy"}
            fetchPriority={eager ? "high" : "auto"}
            decoding="async"
            onError={() => setFailed(true)}
            className="h-full w-full object-cover transition duration-200 group-hover:scale-[1.02] motion-reduce:group-hover:scale-100"
          />
        )}
        <span className="absolute right-3 bottom-3 inline-flex items-center gap-1 rounded-full bg-black/70 px-2.5 py-1 text-[10px] font-medium text-white">
          <Presentation className="h-3 w-3" aria-hidden="true" />
          {item.pageCount} {t.agents.showcase.slides}
        </span>
      </button>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <span className="text-[11px] font-medium text-teal-700 dark:text-teal-300">
          {localized(item.tag, locale)}
        </span>
        <h3 className="text-foreground text-sm leading-6 font-semibold">
          {title}
        </h3>
        <p className="text-muted-foreground line-clamp-3 text-xs leading-5">
          {localized(item.description, locale)}
        </p>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {item.features.map((feature) => (
            <span
              key={feature.en}
              className="text-muted-foreground inline-flex items-center gap-1 text-[10px]"
            >
              <FileCheck2
                className="h-3 w-3 text-teal-600 dark:text-teal-300"
                aria-hidden="true"
              />
              {localized(feature, locale)}
            </span>
          ))}
        </div>
        <div className="mt-auto flex items-end justify-between gap-3 pt-2">
          <span className="text-muted-foreground text-[11px]">
            {localized(item.styleLabel, locale)} ·{" "}
            {formatBytes(item.pptxBytes, locale)}
          </span>
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
      className="bg-card/70 border-border flex min-w-0 flex-col overflow-hidden rounded-2xl border shadow-sm backdrop-blur-sm transition duration-200 hover:-translate-y-0.5 hover:border-fuchsia-300 hover:shadow-md motion-reduce:hover:translate-y-0 dark:border-white/10 dark:bg-white/5 dark:hover:border-fuchsia-400/50"
    >
      <button
        type="button"
        data-testid={`showcase-open-${item.id}`}
        aria-label={`${t.agents.showcase.openCase}: ${title}`}
        onClick={(event) => onOpen(item, event.currentTarget)}
        className={cn(
          "group relative block w-full overflow-hidden bg-neutral-950 text-left focus-visible:ring-2 focus-visible:ring-fuchsia-500 focus-visible:outline-none",
          item.category === "poster" ? "aspect-[4/5]" : "aspect-video",
        )}
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
            className="h-full w-full object-cover transition duration-200 group-hover:scale-[1.02] motion-reduce:group-hover:scale-100"
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
      <div className="flex flex-1 flex-col gap-2 p-4">
        <span className="text-[11px] font-medium text-fuchsia-700 dark:text-fuchsia-300">
          {localized(item.tag, locale)}
        </span>
        <h3 className="text-foreground text-sm leading-6 font-semibold">
          {title}
        </h3>
        <p className="text-muted-foreground line-clamp-2 text-xs leading-5">
          {localized(item.description, locale)}
        </p>
        <div className="text-muted-foreground text-[10px]">
          {item.width} × {item.height} · {localized(item.styleLabel, locale)}
        </div>
        <div className="mt-auto flex justify-end pt-1">
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

export function showcaseCardClass(columns: "two" | "three") {
  return cn(
    "grid gap-4",
    columns === "two" ? "sm:grid-cols-2" : "sm:grid-cols-2 xl:grid-cols-3",
  );
}
