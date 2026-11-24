"use client";

import {
  ArrowLeftIcon,
  BotIcon,
  CheckCircleIcon,
  InfoIcon,
  MoreHorizontalIcon,
  SaveIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  PromptInput,
  PromptInputFooter,
  PromptInputProvider,
  PromptInputSubmit,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import type { Agent } from "@/core/agents";
import {
  AgentNameCheckError,
  checkAgentName,
  createAgent,
  getAgent,
} from "@/core/agents/api";
import { useI18n } from "@/core/i18n/hooks";
import { SEND_BUSY_MESSAGE } from "@/core/threads/errors";
import { useThreadStream } from "@/core/threads/hooks";
import { SendRejectedError } from "@/core/threads/submission";
import { uuid } from "@/core/utils/uuid";
import { isIMEComposing } from "@/lib/ime";
import { cn } from "@/lib/utils";

type Step = "name" | "chat";
type SetupAgentStatus = "idle" | "requested" | "completed";

const NAME_RE = /^[A-Za-z0-9-]+$/;
const SAVE_HINT_STORAGE_KEY = "deerflow.agent-create.save-hint-seen";
const AGENT_READ_RETRY_DELAYS_MS = [200, 500, 1_000, 2_000];

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function getAgentWithRetry(agentName: string) {
  for (const delay of [0, ...AGENT_READ_RETRY_DELAYS_MS]) {
    if (delay > 0) {
      await wait(delay);
    }

    try {
      return await getAgent(agentName);
    } catch {
      // Retry until the write settles or the attempts are exhausted.
    }
  }

  return null;
}

function getCreateAgentErrorMessage(
  error: unknown,
  networkErrorMessage: string,
  fallbackMessage: string,
) {
  if (error instanceof TypeError && error.message === "Failed to fetch") {
    return networkErrorMessage;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallbackMessage;
}

export default function NewAgentPage() {
  const { t } = useI18n();
  const router = useRouter();

  const [step, setStep] = useState<Step>("name");
  const [nameInput, setNameInput] = useState("");
  const [nameError, setNameError] = useState("");
  const [isCheckingName, setIsCheckingName] = useState(false);
  const [isCreatingAgent, setIsCreatingAgent] = useState(false);
  const [agentName, setAgentName] = useState("");
  const [agent, setAgent] = useState<Agent | null>(null);
  const [showSaveHint, setShowSaveHint] = useState(false);
  const [setupAgentStatus, setSetupAgentStatus] =
    useState<SetupAgentStatus>("idle");

  const threadId = useMemo(() => uuid(), []);

  const { thread, sendMessage } = useThreadStream({
    threadId: undefined,
    context: {
      mode: "flash",
      is_bootstrap: true,
    },
    onFinish() {
      if (!agent && setupAgentStatus === "requested") {
        setSetupAgentStatus("idle");
      }
    },
    onToolEnd({ name }) {
      if (name !== "setup_agent" || !agentName) return;
      setSetupAgentStatus("completed");
      void getAgentWithRetry(agentName).then((fetched) => {
        if (fetched) {
          setAgent(fetched);
          return;
        }

        toast.error(t.agents.agentCreatedPendingRefresh);
      });
    },
  });

  useEffect(() => {
    if (typeof window === "undefined" || step !== "chat") {
      return;
    }
    if (window.localStorage.getItem(SAVE_HINT_STORAGE_KEY) === "1") {
      return;
    }
    setShowSaveHint(true);
    window.localStorage.setItem(SAVE_HINT_STORAGE_KEY, "1");
  }, [step]);

  const handleConfirmName = useCallback(async () => {
    const trimmed = nameInput.trim();
    if (!trimmed) return;
    if (!NAME_RE.test(trimmed)) {
      setNameError(t.agents.nameStepInvalidError);
      return;
    }

    setNameError("");
    setIsCheckingName(true);
    try {
      const result = await checkAgentName(trimmed);
      if (!result.available) {
        setNameError(t.agents.nameStepAlreadyExistsError);
        return;
      }
    } catch (err) {
      if (
        err instanceof AgentNameCheckError &&
        err.reason === "backend_unreachable"
      ) {
        setNameError(t.agents.nameStepNetworkError);
      } else {
        setNameError(t.agents.nameStepCheckError);
      }
      return;
    } finally {
      setIsCheckingName(false);
    }

    setIsCreatingAgent(true);
    try {
      await createAgent({
        name: trimmed,
        description: "",
        soul: "",
      });
    } catch (err) {
      setNameError(
        getCreateAgentErrorMessage(
          err,
          t.agents.nameStepNetworkError,
          t.agents.nameStepCheckError,
        ),
      );
      return;
    } finally {
      setIsCreatingAgent(false);
    }

    setAgentName(trimmed);
    setStep("chat");
    // bootstrap 的 rejection（受理前失败 / busy）由 hook 统一提示；这里只观察，页面已进入 chat 步
    await sendMessage(threadId, {
      text: t.agents.nameStepBootstrapMessage.replace("{name}", trimmed),
      files: [],
    }).catch(() => undefined);
  }, [
    nameInput,
    sendMessage,
    t.agents.nameStepAlreadyExistsError,
    t.agents.nameStepNetworkError,
    t.agents.nameStepBootstrapMessage,
    t.agents.nameStepCheckError,
    t.agents.nameStepInvalidError,
    threadId,
  ]);

  const handleNameKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !isIMEComposing(e)) {
      e.preventDefault();
      void handleConfirmName();
    }
  };

  // 普通聊天：accepted Promise 交给 registry PromptInput——受理后才清空正文，
  // busy 由 sendMessage 拒绝（不在这里提前 return，否则会被当成同步成功而清空）
  const handleChatSubmit = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return Promise.resolve();
      return sendMessage(
        threadId,
        { text: trimmed, files: [] },
        { agent_name: agentName },
      );
    },
    [agentName, sendMessage, threadId],
  );

  const handleSaveAgent = useCallback(async () => {
    if (
      !agentName ||
      agent ||
      thread.isLoading ||
      setupAgentStatus !== "idle"
    ) {
      return;
    }

    setSetupAgentStatus("requested");
    setShowSaveHint(false);
    try {
      await sendMessage(
        threadId,
        { text: t.agents.saveCommandMessage, files: [] },
        { agent_name: agentName },
        { additionalKwargs: { hide_from_ui: true } },
      );
      toast.success(t.agents.saveRequested);
    } catch (error) {
      // failed / stopped 由 hook 统一提示；busy 是 hook 刻意不提示的（假设正文还留在
      // 输入框）——但这里是隐藏的保存命令，没有可重试的输入，需要自己提示一次
      if (SendRejectedError.rejectionKind(error) === "busy") {
        toast.error(error instanceof Error ? error.message : SEND_BUSY_MESSAGE);
      }
      setSetupAgentStatus("idle");
    }
  }, [
    agent,
    agentName,
    sendMessage,
    setupAgentStatus,
    t.agents.saveCommandMessage,
    t.agents.saveRequested,
    thread.isLoading,
    threadId,
  ]);

  const header = (
    <header className="flex shrink-0 items-center justify-between gap-3 border-b px-4 py-3">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => router.push("/workspace/agents")}
        >
          <ArrowLeftIcon className="h-4 w-4" />
        </Button>
        <h1 className="text-sm font-semibold">{t.agents.createPageTitle}</h1>
      </div>

      {step === "chat" ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label={t.agents.more}>
              <MoreHorizontalIcon className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onSelect={() => void handleSaveAgent()}
              disabled={[
                Boolean(agent),
                thread.isLoading,
                setupAgentStatus !== "idle",
              ].some(Boolean)}
            >
              <SaveIcon className="h-4 w-4" />
              {setupAgentStatus === "requested"
                ? t.agents.saving
                : t.agents.save}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
    </header>
  );

  if (step === "name") {
    return (
      <div className="flex size-full flex-col">
        {header}
        <main className="flex flex-1 flex-col items-center justify-center px-4">
          <div className="w-full max-w-sm space-y-8">
            <div className="space-y-3 text-center">
              <div className="bg-primary/10 mx-auto flex h-14 w-14 items-center justify-center rounded-full">
                <BotIcon className="text-primary h-7 w-7" />
              </div>
              <div className="space-y-1">
                <h2 className="text-xl font-semibold">
                  {t.agents.nameStepTitle}
                </h2>
                <p className="text-muted-foreground text-sm">
                  {t.agents.nameStepHint}
                </p>
              </div>
            </div>

            <div className="space-y-3">
              <Input
                autoFocus
                placeholder={t.agents.nameStepPlaceholder}
                value={nameInput}
                onChange={(e) => {
                  setNameInput(e.target.value);
                  setNameError("");
                }}
                onKeyDown={handleNameKeyDown}
                className={cn(nameError && "border-destructive")}
              />
              {nameError ? (
                <p className="text-destructive text-sm">{nameError}</p>
              ) : null}
              <Button
                className="w-full"
                onClick={() => void handleConfirmName()}
                disabled={
                  !nameInput.trim() || isCheckingName || isCreatingAgent
                }
              >
                {t.agents.nameStepContinue}
              </Button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <PromptInputProvider>
      <ThreadContext.Provider value={{ thread }}>
        <ArtifactsProvider>
          <div className="flex size-full flex-col">
            {header}

            <main className="flex min-h-0 flex-1 flex-col">
              {showSaveHint ? (
                <div className="px-4 pt-4">
                  <div className="mx-auto w-full max-w-(--container-width-md)">
                    <Alert>
                      <InfoIcon className="h-4 w-4" />
                      <AlertDescription>{t.agents.saveHint}</AlertDescription>
                    </Alert>
                  </div>
                </div>
              ) : null}

              <div className="flex min-h-0 flex-1 justify-center">
                <MessageList
                  className={cn("size-full", showSaveHint ? "pt-4" : "pt-10")}
                  threadId={threadId}
                  thread={thread}
                />
              </div>

              <div className="bg-background flex shrink-0 justify-center border-t px-4 py-4">
                <div className="w-full max-w-(--container-width-md)">
                  {agent ? (
                    <div className="flex flex-col items-center gap-4 rounded-2xl border py-8 text-center">
                      <CheckCircleIcon className="text-primary h-10 w-10" />
                      <p className="font-semibold">{t.agents.agentCreated}</p>
                      <div className="flex gap-2">
                        <Button
                          onClick={() =>
                            router.push(
                              `/workspace/agents/${agentName}/chats/new`,
                            )
                          }
                        >
                          {t.agents.startChatting}
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => router.push("/workspace/agents")}
                        >
                          {t.agents.backToGallery}
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <PromptInput
                      // 把 accepted Promise 原样交回 registry：受理后才清空正文
                      onSubmit={({ text }) => handleChatSubmit(text)}
                    >
                      <PromptInputTextarea
                        autoFocus
                        placeholder={t.agents.createPageSubtitle}
                        disabled={thread.isLoading}
                      />
                      <PromptInputFooter className="justify-end">
                        <PromptInputSubmit disabled={thread.isLoading} />
                      </PromptInputFooter>
                    </PromptInput>
                  )}
                </div>
              </div>
            </main>
          </div>
        </ArtifactsProvider>
      </ThreadContext.Provider>
    </PromptInputProvider>
  );
}
