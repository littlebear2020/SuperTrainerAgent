"use client";

import { CheckCircle2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type {
  DeckCase,
  PodcastCase,
  ShowcaseAgent,
  VideoCase,
  VisualCase,
} from "@/core/agents/showcase";
import { useI18n } from "@/core/i18n/hooks";

import { ShowcaseAudioPlayer } from "./showcase-audio-player";
import {
  DeckCard,
  showcaseCardClass,
  VideoCard,
  VisualCard,
} from "./showcase-cards";
import {
  DeckDialog,
  PodcastDialog,
  VideoDialog,
  VisualDialog,
} from "./showcase-dialogs";
import { localized, type ShowcaseLocale } from "./showcase-utils";

export function AgentShowcase({
  agent,
  disabled,
  onUsePrompt,
}: {
  agent: ShowcaseAgent;
  disabled: boolean;
  onUsePrompt: (prompt: string) => void;
}) {
  const { locale, t } = useI18n();
  const language: ShowcaseLocale = locale === "zh-CN" ? "zh" : "en";
  const [activeVideo, setActiveVideo] = useState<VideoCase | null>(null);
  const [activeDeck, setActiveDeck] = useState<{
    item: DeckCase;
    initialSlide: number;
  } | null>(null);
  const [activeVisual, setActiveVisual] = useState<VisualCase | null>(null);
  const [activePodcast, setActivePodcast] = useState<PodcastCase | null>(null);
  const [visualFilter, setVisualFilter] = useState<
    VisualCase["category"] | "all"
  >("all");
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const focusTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const visibleCases = agent.cases.filter(
    (item) =>
      item.kind !== "visual" ||
      visualFilter === "all" ||
      item.category === visualFilter,
  );
  const podcastCases = agent.cases.filter(
    (item): item is PodcastCase => item.kind === "podcast",
  );
  const hasVisualCases = agent.cases.some((item) => item.kind === "visual");
  const hasPodcastCases = podcastCases.length > 0;
  const gridCases = visibleCases.filter((item) => item.kind !== "podcast");
  const filterLabels: Record<VisualCase["category"] | "all", string> = {
    all: t.agents.showcase.filterAll,
    poster: t.agents.showcase.filterPoster,
    card: t.agents.showcase.filterCard,
    comic: t.agents.showcase.filterComic,
    infographic: t.agents.showcase.filterInfographic,
    diagram: t.agents.showcase.filterDiagram,
  };
  const availableCategories = new Set(
    agent.cases
      .filter((item): item is VisualCase => item.kind === "visual")
      .map((item) => item.category),
  );
  const visualFilters = (
    ["all", "poster", "card", "comic", "infographic", "diagram"] as const
  )
    .filter((value) => value === "all" || availableCategories.has(value))
    .map((value) => [value, filterLabels[value]] as const);

  useEffect(() => {
    return () => {
      if (focusTimeoutRef.current !== null) {
        clearTimeout(focusTimeoutRef.current);
      }
    };
  }, []);

  const closeDialog = () => {
    if (focusTimeoutRef.current !== null) {
      clearTimeout(focusTimeoutRef.current);
    }
    const trigger = triggerRef.current;
    setActiveVideo(null);
    setActiveDeck(null);
    setActiveVisual(null);
    setActivePodcast(null);
    focusTimeoutRef.current = setTimeout(() => trigger?.focus(), 0);
  };

  return (
    <>
      <section className="border-border/80 grid min-w-0 gap-7 border-t pt-6 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-8 xl:grid-cols-[260px_minmax(0,1fr)] xl:gap-10 dark:border-white/10">
        <div className="order-2 lg:sticky lg:top-6 lg:order-1 lg:self-start">
          <p className="text-muted-foreground text-[10px] font-semibold tracking-[0.2em]">
            {t.agents.showcase.howItWorks}
          </p>
          <h2 className="text-foreground mt-3 text-[26px] leading-[1.25] font-bold tracking-[-0.035em]">
            {localized(agent.howTitle, language)}
          </h2>
          <p className="text-muted-foreground mt-3 text-[13px] leading-6">
            {localized(agent.howDescription, language)}
          </p>
          <ol className="mt-7 grid gap-5 sm:grid-cols-3 lg:grid-cols-1">
            {agent.steps.map((step, index) => (
              <li key={step.title.en} className="flex gap-3">
                <span className="text-muted-foreground pt-0.5 font-mono text-[11px] tabular-nums">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3 className="text-foreground text-[13px] font-semibold">
                    {localized(step.title, language)}
                  </h3>
                  <p className="text-muted-foreground mt-1.5 text-xs leading-5">
                    {localized(step.description, language)}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>

        <div className="order-1 min-w-0 lg:order-2">
          <div className="border-border/80 mb-4 flex flex-wrap items-center justify-between gap-2 border-b pb-3 dark:border-white/10">
            <h2 className="text-foreground text-[15px] font-semibold">
              {localized(agent.caseLabel, language)}
              <span className="text-muted-foreground ml-2 text-xs font-normal">
                {agent.cases.length} {t.agents.showcase.caseUnit}
              </span>
            </h2>
            <span className="showcase-tag inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[10px] font-medium">
              <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
              {localized(agent.badge, language)}
            </span>
          </div>
          <p className="text-muted-foreground sr-only">
            {t.agents.showcase.casesDescription}
          </p>
          {hasVisualCases ? (
            <div
              className="mb-4 flex gap-2 overflow-x-auto pb-1"
              aria-label={t.agents.showcase.casesTitle}
            >
              {visualFilters.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  data-testid={`showcase-filter-${value}`}
                  aria-pressed={visualFilter === value}
                  onClick={() => setVisualFilter(value)}
                  className="border-border min-h-9 shrink-0 rounded-full border px-3 text-xs whitespace-nowrap transition hover:border-neutral-400 focus-visible:ring-2 focus-visible:ring-rose-500 focus-visible:outline-none aria-pressed:border-neutral-900 aria-pressed:bg-neutral-900 aria-pressed:text-white dark:border-white/10 dark:aria-pressed:border-neutral-100 dark:aria-pressed:bg-neutral-100 dark:aria-pressed:text-neutral-900"
                >
                  {label}
                </button>
              ))}
              <span className="text-muted-foreground ml-auto shrink-0 self-center text-[11px] whitespace-nowrap">
                {visibleCases.length} {t.agents.showcase.itemUnit}
              </span>
            </div>
          ) : null}
          {hasPodcastCases ? (
            <ShowcaseAudioPlayer
              items={podcastCases}
              locale={language}
              disabled={disabled}
              onUsePrompt={onUsePrompt}
              onTranscript={(podcast, trigger) => {
                triggerRef.current = trigger;
                setActivePodcast(podcast);
              }}
            />
          ) : null}
          {gridCases.length > 0 ? (
            <div
              data-testid="agent-guide-cases"
              data-showcase
              className={showcaseCardClass(gridCases[0]?.kind ?? "video")}
            >
              {gridCases.map((item, index) => {
                if (item.kind === "video") {
                  return (
                    <VideoCard
                      key={item.id}
                      item={item}
                      locale={language}
                      eager={index === 0}
                      disabled={disabled}
                      onOpen={(video, trigger) => {
                        triggerRef.current = trigger;
                        setActiveVideo(video);
                      }}
                      onUsePrompt={onUsePrompt}
                    />
                  );
                }
                if (item.kind === "deck") {
                  return (
                    <DeckCard
                      key={item.id}
                      item={item}
                      locale={language}
                      eager={index === 0}
                      disabled={disabled}
                      onOpen={(deck, trigger, slideIndex = 0) => {
                        triggerRef.current = trigger;
                        setActiveDeck({ item: deck, initialSlide: slideIndex });
                      }}
                      onUsePrompt={onUsePrompt}
                    />
                  );
                }
                if (item.kind === "visual") {
                  return (
                    <VisualCard
                      key={item.id}
                      item={item}
                      locale={language}
                      eager={index === 0}
                      disabled={disabled}
                      onOpen={(visual, trigger) => {
                        triggerRef.current = trigger;
                        setActiveVisual(visual);
                      }}
                      onUsePrompt={onUsePrompt}
                    />
                  );
                }
                return null;
              })}
            </div>
          ) : null}
          <p className="text-muted-foreground mt-4 flex gap-2 text-[11px] leading-5">
            <CheckCircle2
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500"
              aria-hidden="true"
            />
            {localized(agent.note, language)}
          </p>
        </div>
      </section>
      {activeVideo ? (
        <VideoDialog
          item={activeVideo}
          locale={language}
          onClose={closeDialog}
        />
      ) : null}
      {activeDeck ? (
        <DeckDialog
          key={activeDeck.item.id}
          item={activeDeck.item}
          initialSlide={activeDeck.initialSlide}
          locale={language}
          onClose={closeDialog}
        />
      ) : null}
      {activeVisual ? (
        <VisualDialog
          item={activeVisual}
          locale={language}
          onClose={closeDialog}
        />
      ) : null}
      {activePodcast ? (
        <PodcastDialog
          item={activePodcast}
          locale={language}
          onClose={closeDialog}
        />
      ) : null}
    </>
  );
}
