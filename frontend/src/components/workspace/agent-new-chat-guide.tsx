"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { agentAppearance } from "@/core/agents/appearance";
import type { AgentNewChatGuideId } from "@/core/agents/new-chat";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export function AgentNewChatGuide({
  agentName,
  handoffReady,
  status,
  disabled = false,
  onRetry,
  children,
}: {
  agentName: AgentNewChatGuideId;
  handoffReady: boolean;
  status: "loading" | "error" | "ready";
  disabled?: boolean;
  onRetry?: () => void;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const guide = t.agents.newChatGuides[agentName];
  const shared = t.agents.newChatGuide;
  const appearance = agentAppearance(agentName);
  const Icon = appearance.icon;

  return (
    <section
      data-testid="agent-new-chat-guide"
      className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-8 sm:px-6"
    >
      <div className="flex flex-col items-center gap-3 text-center">
        <span
          className={cn(
            "flex h-12 w-12 items-center justify-center rounded-2xl shadow-sm",
            appearance.bg,
          )}
        >
          <Icon className={cn("h-6 w-6", appearance.text)} />
        </span>
        <p className="text-xs font-medium tracking-[0.18em] text-indigo-500 uppercase">
          {guide.displayName}
        </p>
        <div className="space-y-2">
          <h1 className="text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl">
            {guide.title}
          </h1>
          <p className="text-muted-foreground mx-auto max-w-2xl text-sm leading-6 sm:text-base">
            {guide.description}
          </p>
        </div>
      </div>

      {status === "ready" ? (
        <div className="space-y-3">
          <div className="text-center">
            <h2 className="text-sm font-semibold text-slate-800">
              {shared.casesTitle}
            </h2>
            <p className="text-muted-foreground mt-1 text-xs">
              {shared.casesDescription}
            </p>
          </div>
          <div
            data-testid="agent-guide-cases"
            className="grid gap-3 sm:grid-cols-2"
          >
            {guide.cases.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-label={item.title}
                disabled={!handoffReady || disabled}
                className="group rounded-2xl border border-indigo-100/80 bg-white/80 p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-300 hover:shadow-md focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
              >
                <span className="block text-sm font-semibold text-slate-900 group-hover:text-indigo-700">
                  {item.title}
                </span>
                <span className="text-muted-foreground mt-1 block text-xs leading-5">
                  {item.description}
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div
          role="status"
          className="rounded-2xl border border-indigo-100/80 bg-white/80 p-5 text-center text-sm text-slate-600"
        >
          <p>{status === "loading" ? shared.loading : shared.loadFailed}</p>
          {status === "error" && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={onRetry}
            >
              {shared.retry}
            </Button>
          )}
        </div>
      )}

      <div data-testid="agent-guide-input">{children}</div>

      <section
        data-testid="agent-guide-tips"
        className="rounded-2xl border border-white/70 bg-white/45 px-5 py-4"
      >
        <h2 className="text-xs font-semibold text-slate-700">
          {shared.tipsTitle}
        </h2>
        <ul className="text-muted-foreground mt-2 grid gap-2 text-xs leading-5 sm:grid-cols-3">
          {guide.tips.map((tip) => (
            <li key={tip} className="flex gap-2">
              <span aria-hidden="true" className="text-indigo-400">
                •
              </span>
              <span>{tip}</span>
            </li>
          ))}
        </ul>
      </section>
    </section>
  );
}
