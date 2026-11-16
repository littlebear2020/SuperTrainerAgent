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
  const [activeDeck, setActiveDeck] = useState<DeckCase | null>(null);
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
      <section className="grid min-w-0 gap-6 lg:grid-cols-[280px_minmax(0,1fr)] lg:gap-8">
        <div className="order-2 lg:order-1">
          <p className="text-[10px] font-semibold tracking-[0.2em] text-indigo-500 dark:text-indigo-300">
            {t.agents.showcase.howItWorks}
          </p>
          <h2 className="text-foreground mt-2 text-lg font-semibold tracking-tight">
            {localized(agent.howTitle, language)}
          </h2>
          <p className="text-muted-foreground mt-2 text-xs leading-5">
            {localized(agent.howDescription, language)}
          </p>
          <ol className="mt-5 grid gap-4 sm:grid-cols-3 lg:grid-cols-1">
            {agent.steps.map((step, index) => (
              <li key={step.title.en} className="flex gap-3">
                <span className="text-xs font-semibold text-indigo-500 tabular-nums dark:text-indigo-300">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3 className="text-foreground text-xs font-semibold">
                    {localized(step.title, language)}
                  </h3>
                  <p className="text-muted-foreground mt-1 text-[11px] leading-5">
                    {localized(step.description, language)}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>

        <div className="order-1 min-w-0 lg:order-2">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
            <div>
              <span className="text-[10px] font-semibold tracking-[0.16em] text-indigo-500 uppercase dark:text-indigo-300">
                {localized(agent.caseLabel, language)}
              </span>
              <h2 className="text-foreground mt-1 text-lg font-semibold">
                {t.agents.showcase.casesTitle}
                <span className="text-muted-foreground ml-2 text-xs font-normal">
                  {agent.cases.length} {t.agents.showcase.caseUnit}
                </span>
              </h2>
            </div>
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200/70 bg-emerald-50/70 px-2.5 py-1 text-[10px] font-medium text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-300">
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
                  className="border-border min-h-9 shrink-0 rounded-full border px-3 text-xs whitespace-nowrap transition hover:border-fuchsia-300 focus-visible:ring-2 focus-visible:ring-fuchsia-500 focus-visible:outline-none aria-pressed:border-fuchsia-500 aria-pressed:bg-fuchsia-50 aria-pressed:text-fuchsia-700 dark:border-white/10 dark:aria-pressed:border-fuchsia-400 dark:aria-pressed:bg-fuchsia-400/10 dark:aria-pressed:text-fuchsia-300"
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
              className={showcaseCardClass(hasVisualCases ? "three" : "two")}
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
                      onOpen={(deck, trigger) => {
                        triggerRef.current = trigger;
                        setActiveDeck(deck);
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
        <DeckDialog item={activeDeck} locale={language} onClose={closeDialog} />
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
