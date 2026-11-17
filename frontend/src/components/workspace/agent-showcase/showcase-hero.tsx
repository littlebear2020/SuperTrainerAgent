"use client";

import type { ShowcaseAgent } from "@/core/agents/showcase";
import { useI18n } from "@/core/i18n/hooks";

import { type ShowcaseLocale } from "./showcase-utils";

import styles from "./agent-showcase.module.css";

export function ShowcaseHero({ agent }: { agent: ShowcaseAgent }) {
  const { locale } = useI18n();
  const language: ShowcaseLocale = locale === "zh-CN" ? "zh" : "en";

  return (
    <div
      data-testid="showcase-hero"
      className="mx-auto flex w-full max-w-[860px] flex-col items-center gap-3 text-center sm:flex-row sm:items-center sm:justify-start sm:gap-9 sm:text-left"
    >
      <div className="relative flex w-[175px] shrink-0 items-end justify-center sm:w-[205px] xl:w-[230px]">
        <span aria-hidden="true" className={styles.heroGlow} />
        <img
          src={agent.character}
          alt=""
          width={720}
          height={1000}
          loading="eager"
          fetchPriority="high"
          decoding="async"
          className="relative z-10 h-[190px] w-full object-contain object-center sm:h-[200px] xl:h-[240px]"
        />
      </div>
      <div className="min-w-0">
        <h1 className="text-foreground text-[26px] leading-[1.34] font-bold tracking-[-0.04em] sm:text-[31px] lg:text-[34px]">
          {agent.heroLine[language][0]}
          <br />
          {agent.heroLine[language][1]}
        </h1>
      </div>
    </div>
  );
}
