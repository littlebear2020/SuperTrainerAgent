"use client";

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
  // The filter row is only meaningful when every case is visual. Mixed agents
  // would otherwise expose filters but fall back to the masonry layout, whose
  // column packing puts filtered cards all in column 1.
  const isVisualOnly =
    agent.cases.length > 0 &&
    agent.cases.every((item) => item.kind === "visual");
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

  // Filtered results are all visual cases; only then may the masonry be
  // replaced by the regular grid (mixed deck/video cards need their own layout).
  const usesFilteredGrid = visualFilter !== "all" && isVisualOnly;

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
      <section
        data-testid="agent-showcase"
        className="border-border/70 mt-6 grid w-full min-w-0 gap-7 border-t pt-4 sm:mt-12 lg:grid-cols-[240px_minmax(0,1fr)] lg:gap-8 xl:mt-24 xl:grid-cols-[280px_minmax(0,1fr)] xl:gap-10 dark:border-white/10"
      >
        <div className="order-2 lg:sticky lg:top-6 lg:order-1 lg:self-start">
          <p className="text-muted-foreground text-[11px] font-semibold tracking-[0.16em]">
            {t.agents.showcase.howItWorks}
          </p>
          <h2 className="text-foreground mt-3 text-[27px] leading-[1.25] font-bold tracking-[-0.035em]">
            {localized(agent.howTitle, language)}
          </h2>
          <p className="text-muted-foreground mt-3 text-[13px] leading-6">
            {localized(agent.howDescription, language)}
          </p>
          <ol className="mt-6 grid gap-5 sm:grid-cols-3 lg:grid-cols-1">
            {agent.steps.map((step, index) => (
              <li key={step.title.en} className="flex gap-3">
                <span className="text-muted-foreground pt-0.5 font-mono text-[11px] tabular-nums">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3 className="text-foreground text-[13.5px] font-semibold">
                    {localized(step.title, language)}
                  </h3>
                  <p className="text-muted-foreground mt-1.5 text-[13px] leading-5">
                    {localized(step.description, language)}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>

        <div className="order-1 min-w-0 lg:order-2">
          <p className="text-muted-foreground sr-only">
            {t.agents.showcase.casesDescription}
          </p>
          {isVisualOnly ? (
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
                  onClick={() =>
                    setVisualFilter(
                      visualFilter === value && value !== "all" ? "all" : value,
                    )
                  }
                  className="border-border min-h-9 shrink-0 cursor-pointer rounded-full border px-3 text-xs whitespace-nowrap transition hover:border-neutral-400 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none aria-pressed:border-neutral-900 aria-pressed:bg-neutral-900 aria-pressed:text-white dark:border-white/10 dark:aria-pressed:border-neutral-100 dark:aria-pressed:bg-neutral-100 dark:aria-pressed:text-neutral-900"
                >
                  {label}
                </button>
              ))}
              <span className="text-muted-foreground ml-auto shrink-0 self-center text-[11px] whitespace-nowrap">
                {visualFilter === "all"
                  ? visibleCases.length
                  : `${visibleCases.length} / ${agent.cases.length}`}{" "}
                {t.agents.showcase.itemUnit}
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
              className={
                // A filtered subset must fill the row: CSS multi-column packs
                // both remaining cards into column 1, leaving columns 2–3 empty
                // and looking like a single result.
                usesFilteredGrid
                  ? showcaseCardClass("video")
                  : showcaseCardClass(gridCases[0]?.kind ?? "video")
              }
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
