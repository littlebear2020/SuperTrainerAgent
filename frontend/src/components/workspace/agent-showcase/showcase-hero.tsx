"use client";

import type { ShowcaseAgent } from "@/core/agents/showcase";
import { useI18n } from "@/core/i18n/hooks";

import { localized, type ShowcaseLocale } from "./showcase-utils";

import styles from "./agent-showcase.module.css";

export function ShowcaseHero({ agent }: { agent: ShowcaseAgent }) {
  const { locale } = useI18n();
  const language: ShowcaseLocale = locale === "zh-CN" ? "zh" : "en";

  return (
    <div
      data-testid="showcase-hero"
      className="flex w-full flex-col items-center gap-3 text-center sm:flex-row sm:items-center sm:justify-start sm:gap-9 sm:text-left"
    >
      <div className="relative flex shrink-0 items-end justify-center">
        <span aria-hidden="true" className={styles.heroGlow} />
        <img
          src={agent.character}
          alt=""
          width={720}
          height={1000}
          loading="eager"
          fetchPriority="high"
          decoding="async"
          className="relative z-10 h-[170px] w-auto max-w-[220px] object-contain object-bottom sm:h-[210px] sm:max-w-[240px] xl:h-[242px] xl:max-w-[260px]"
        />
      </div>
      <div className="min-w-0 space-y-2 sm:space-y-3">
        <div className="flex items-center justify-center gap-2.5 sm:justify-start">
          <span
            aria-hidden="true"
            className="showcase-accent-text h-0.5 w-6 shrink-0 rounded-full bg-current sm:w-7"
          />
          <p className="showcase-accent-text text-[11px] font-semibold tracking-[0.04em] sm:text-xs">
            {localized(agent.label, language)}
          </p>
        </div>
        <h1 className="text-foreground text-[25px] leading-[1.36] font-bold tracking-[-0.035em] sm:text-[30px] lg:text-[33px]">
          {agent.heroLine[language][0]}
          <br />
          {agent.heroLine[language][1]}
        </h1>
      </div>
    </div>
  );
}
