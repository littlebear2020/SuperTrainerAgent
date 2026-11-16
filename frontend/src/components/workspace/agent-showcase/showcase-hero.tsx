"use client";

import { agentAppearance } from "@/core/agents/appearance";
import type { ShowcaseAgent } from "@/core/agents/showcase";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { localized, type ShowcaseLocale } from "./showcase-utils";

export function ShowcaseHero({ agent }: { agent: ShowcaseAgent }) {
  const { locale } = useI18n();
  const language: ShowcaseLocale = locale === "zh-CN" ? "zh" : "en";
  const appearance = agentAppearance(agent.id);

  return (
    <div
      data-testid="showcase-hero"
      className="mx-auto flex w-full max-w-4xl items-center justify-center gap-4 text-left sm:gap-7"
    >
      <img
        src={agent.character}
        alt=""
        width={720}
        height={1000}
        loading="eager"
        fetchPriority="high"
        decoding="async"
        className="h-[150px] w-[132px] shrink-0 rounded-[24px] border border-indigo-200/50 bg-indigo-50 object-cover object-top shadow-lg shadow-slate-900/10 sm:h-[190px] sm:w-[176px] sm:rounded-[28px] dark:border-white/15"
      />
      <div className="min-w-0 space-y-2 sm:space-y-3">
        <div className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className={cn("h-0.5 w-6 rounded-full bg-current", appearance.text)}
          />
          <p
            className={cn(
              "text-[10px] font-semibold tracking-[0.18em] uppercase sm:text-xs",
              appearance.text,
            )}
          >
            {localized(agent.label, language)}
          </p>
        </div>
        <h1 className="text-foreground text-[21px] leading-[1.4] font-semibold tracking-tight sm:text-[28px] sm:leading-[1.35]">
          {agent.heroLine[language][0]}
          <br />
          {agent.heroLine[language][1]}
        </h1>
      </div>
    </div>
  );
}
