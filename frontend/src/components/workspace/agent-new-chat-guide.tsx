"use client";

import { useState, type ReactNode } from "react";

import { usePromptInputController } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { agentAppearance } from "@/core/agents/appearance";
import type { AgentNewChatGuideId } from "@/core/agents/new-chat";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export function AgentNewChatGuide({
  agentName,
  handoffReady,
  handoffKey,
  status,
  disabled = false,
  onRetry,
  children,
}: {
  agentName: AgentNewChatGuideId;
  handoffReady: boolean;
  handoffKey: string;
  status: "loading" | "error" | "ready";
  disabled?: boolean;
  onRetry?: () => void;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const { textInput } = usePromptInputController();
  const [pendingSelection, setPendingSelection] = useState<{
    prompt: string;
    handoffKey: string;
  } | null>(null);
  const guide = t.agents.newChatGuides[agentName];
  const shared = t.agents.newChatGuide;
  const appearance = agentAppearance(agentName);
  const Icon = appearance.icon;
  const confirmOpen =
    pendingSelection !== null &&
    pendingSelection.handoffKey === handoffKey &&
    handoffReady &&
    status === "ready" &&
    !disabled;

  const focusComposer = () => {
    const composer = document.querySelector<HTMLTextAreaElement>(
      "textarea[name='message']",
    );
    if (!composer) return;
    composer.focus();
    composer.setSelectionRange(composer.value.length, composer.value.length);
  };

  const applyPrompt = (prompt: string, append: boolean) => {
    const current = textInput.value;
    const separator = append && current && !current.endsWith("\n") ? "\n" : "";
    textInput.setInput(append ? current + separator + prompt : prompt);
    setPendingSelection(null);
    setTimeout(focusComposer, 0);
  };

  const selectCase = (prompt: string) => {
    if (!handoffReady || disabled || status !== "ready") return;
    if (textInput.value.trim()) {
      setPendingSelection({ prompt, handoffKey });
      return;
    }
    applyPrompt(prompt, false);
  };

  const applyPending = (append: boolean) => {
    if (
      pendingSelection?.handoffKey !== handoffKey ||
      !handoffReady ||
      disabled ||
      status !== "ready"
    ) {
      return;
    }
    applyPrompt(pendingSelection.prompt, append);
  };

  return (
    <>
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
          <p className="text-xs font-medium tracking-[0.18em] text-indigo-500 uppercase dark:text-indigo-300">
            {guide.displayName}
          </p>
          <div className="space-y-2">
            <h1 className="text-foreground text-3xl font-semibold tracking-tight sm:text-4xl">
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
              <h2 className="text-foreground text-sm font-semibold">
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
                  onClick={() => selectCase(item.prompt)}
                  className="group rounded-2xl border border-indigo-100/80 bg-white/80 p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-300 hover:shadow-md focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0 dark:border-white/10 dark:bg-white/5 dark:hover:border-indigo-400/60"
                >
                  <span className="text-foreground block text-sm font-semibold group-hover:text-indigo-700 dark:group-hover:text-indigo-300">
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
            className="text-muted-foreground rounded-2xl border border-indigo-100/80 bg-white/80 p-5 text-center text-sm dark:border-white/10 dark:bg-white/5"
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
          className="rounded-2xl border border-white/70 bg-white/45 px-5 py-4 dark:border-white/10 dark:bg-white/5"
        >
          <h2 className="text-foreground text-xs font-semibold">
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

      <Dialog
        open={confirmOpen}
        onOpenChange={(open) => {
          if (!open) setPendingSelection(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{shared.draftConfirmTitle}</DialogTitle>
            <DialogDescription>
              {shared.draftConfirmDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setPendingSelection(null)}
            >
              {t.common.cancel}
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => applyPending(true)}
            >
              {shared.draftConfirmAppend}
            </Button>
            <Button type="button" onClick={() => applyPending(false)}>
              {shared.draftConfirmReplace}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
