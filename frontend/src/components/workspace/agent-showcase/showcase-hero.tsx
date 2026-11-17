"use client";

import type { ShowcaseAgent } from "@/core/agents/showcase";
import { useI18n } from "@/core/i18n/hooks";

import { localized, type ShowcaseLocale } from "./showcase-utils";

export function ShowcaseHero({ agent }: { agent: ShowcaseAgent }) {
  const { locale } = useI18n();
  const language: ShowcaseLocale = locale === "zh-CN" ? "zh" : "en";

  return (
    <div
      data-testid="showcase-hero"
      className="mx-auto flex w-full max-w-4xl flex-col items-center justify-center gap-2 text-center sm:flex-row sm:gap-8 sm:text-left"
    >
      <img
        src={agent.character}
        alt=""
        width={720}
        height={1000}
        loading="eager"
        fetchPriority="high"
        decoding="async"
        className="h-[190px] w-[150px] shrink-0 rounded-[24px] border border-indigo-200/50 bg-indigo-50 object-contain shadow-sm shadow-slate-900/10 sm:h-[210px] sm:w-[170px] sm:rounded-[28px] xl:h-[230px] xl:w-[180px] dark:border-white/15"
      />
      <div className="min-w-0 space-y-2 sm:space-y-3">
        <div className="flex items-center justify-center gap-2 sm:justify-start">
          <span
            aria-hidden="true"
            className="showcase-accent-text h-0.5 w-7 rounded-full bg-current"
          />
          <p className="showcase-accent-text text-[10px] font-semibold tracking-[0.18em] uppercase sm:text-xs">
            {localized(agent.label, language)}
          </p>
        </div>
        <h1 className="text-foreground text-[22px] leading-[1.4] font-bold tracking-[-0.035em] sm:text-[30px] sm:leading-[1.38]">
          {agent.heroLine[language][0]}
          <br />
          {agent.heroLine[language][1]}
        </h1>
      </div>
    </div>
  );
}
