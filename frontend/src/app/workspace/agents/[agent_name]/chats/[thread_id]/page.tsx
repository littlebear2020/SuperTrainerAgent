"use client";

import { BotIcon, PlusSquare } from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  usePromptInputController,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { AgentNewChatGuide } from "@/components/workspace/agent-new-chat-guide";
import { AgentWelcome } from "@/components/workspace/agent-welcome";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import { ChatBox, useThreadChat } from "@/components/workspace/chats";
import { ExportTrigger } from "@/components/workspace/export-trigger";
import { InputBox } from "@/components/workspace/input-box";
import {
  MessageList,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
  MESSAGE_LIST_FOLLOWUPS_EXTRA_PADDING_BOTTOM,
} from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { Tooltip } from "@/components/workspace/tooltip";
import {
  isAgentNewChatGuideId,
  resolveAgentName,
  useAgent,
} from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { useNotification } from "@/core/notification/hooks";
import { useThreadSettings } from "@/core/settings";
import { useThreadStream } from "@/core/threads/hooks";
import { useThreadTokenUsage } from "@/core/threads/token-usage";
import { textOfMessage } from "@/core/threads/utils";
import { env } from "@/env";
import { readPendingPrompt } from "@/lib/pending-prompt";
import { cn } from "@/lib/utils";

export default function AgentChatPage() {
  const { t } = useI18n();
  const [showFollowups, setShowFollowups] = useState(false);
  const router = useRouter();

  const { agent_name: agentNameParam } = useParams<{
    agent_name: string;
  }>();

  // 2026-09-09 创意工作合并：旧 5 壳名的历史线程/旧 URL 在此参数归一为
  // creative-studio（useAgent / sendMessage context / history.replaceState 均走
  // 新名），线程照常续跑。不用 router.replace 重定向——本页 router 导航会
  // remount 丢失状态（见下方 onStart 注释），且会干扰 ?msg= 预填逻辑。
  const agent_name = resolveAgentName(agentNameParam);
  const guideAgentName = isAgentNewChatGuideId(agent_name) ? agent_name : null;
  const { threadId, setThreadId, isNewThread, setIsNewThread } =
    useThreadChat();
  const showGuidedNewChat = isNewThread && guideAgentName !== null;
  const {
    agent,
    isLoading: agentLoading,
    error: agentError,
    refetch: refetchAgent,
  } = useAgent(agent_name, {
    preventAutomaticErrorRecovery: showGuidedNewChat,
  });
  const guideStatus = agentLoading
    ? "loading"
    : agentError || !agent
      ? "error"
      : "ready";
  const [settings, setSettings] = useThreadSettings(threadId);
  const { tokenUsageEnabled, tokenBudgetWarnTokens } = useModels();
  // D3 修订（review #4）：run 结束时递增 → 徽章总量重拉（与后端告警同源的
  // thread 全历史聚合）；挂载时首次拉取由 hook 自身承担，无轮询。
  const [tokenUsageRefresh, setTokenUsageRefresh] = useState(0);
  const threadTokenUsage = useThreadTokenUsage(
    isNewThread ? undefined : threadId,
    tokenUsageEnabled,
    tokenUsageRefresh,
  );

  const { showNotification } = useNotification();
  const {
    thread,
    sendMessage,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    context: { ...settings.context, agent_name: agent_name },
    onStart: (createdThreadId) => {
      setThreadId(createdThreadId);
      setIsNewThread(false);
      // ! Important: Never use next.js router for navigation in this case, otherwise it will cause the thread to re-mount and lose all states. Use native history API instead.
      history.replaceState(
        null,
        "",
        `/workspace/agents/${agent_name}/chats/${createdThreadId}`,
      );
    },
    onFinish: (state) => {
      setTokenUsageRefresh((n) => n + 1);
      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages[state.messages.length - 1];
        if (lastMessage) {
          const textContent = textOfMessage(lastMessage);
          if (textContent) {
            body =
              textContent.length > 200
                ? textContent.substring(0, 200) + "..."
                : textContent;
          }
        }
        showNotification(state.title, { body });
      }
    },
  });

  // C1a fix (2026-07-15): re-fill the composed prompt handed off from the video-gen
  // Remotion button (?msg= short / sessionStorage long). Sole reader — the layout no
  // longer calls readPendingPrompt (it would consume sessionStorage + throw the value
  // away on layout-persistence nav). This effect re-runs on every searchParams change
  // (every navigation) and re-fills via the controller's setInput. readPendingPrompt
  // consumes the sessionStorage entry; prefillRunRef prevents re-filling the same prompt.
  const prefillSearchParams = useSearchParams();
  const handoffKey = prefillSearchParams.toString();
  const [handoffDoneKey, setHandoffDoneKey] = useState<string | null>(null);
  const handoffReady = handoffDoneKey === handoffKey;
  const { textInput } = usePromptInputController();
  const prefillRunRef = useRef<string | null>(null);
  useEffect(() => {
    const storage =
      typeof window !== "undefined" ? window.sessionStorage : null;
    const prompt = readPendingPrompt(prefillSearchParams.get("msg"), storage);
    if (prompt && prefillRunRef.current !== prompt) {
      prefillRunRef.current = prompt;
      textInput.setInput(prompt);
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- publish completion of the external handoff read to the guide.
    setHandoffDoneKey(handoffKey);
    // textInput.setInput is a stable useState setter — call directly, no dep on textInput
    // (the controller object is a new ref each render; depending on it would re-run every render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillSearchParams, handoffKey]);

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      void sendMessage(threadId, message, { agent_name });
    },
    [sendMessage, threadId, agent_name],
  );

  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);

  const messageListPaddingBottom = showFollowups
    ? MESSAGE_LIST_DEFAULT_PADDING_BOTTOM +
      MESSAGE_LIST_FOLLOWUPS_EXTRA_PADDING_BOTTOM
    : undefined;

  const demoMode = env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true";
  const guideDisplayName = guideAgentName
    ? t.agents.newChatGuides[guideAgentName].displayName
    : null;
  const inputBox = (
    <InputBox
      className={cn(
        "bg-background/5 w-full",
        !showGuidedNewChat && "-translate-y-4",
      )}
      isNewThread={isNewThread}
      threadId={threadId}
      autoFocus={showGuidedNewChat ? false : isNewThread}
      status={thread.error ? "error" : thread.isLoading ? "streaming" : "ready"}
      context={settings.context}
      extraHeader={
        !showGuidedNewChat &&
        isNewThread && <AgentWelcome agent={agent} agentName={agent_name} />
      }
      agentSkills={agent?.skills}
      agentSuggestionPrompts={agent?.suggestion_prompts ?? null}
      showNewThreadSuggestions={!showGuidedNewChat}
      disabled={demoMode || (showGuidedNewChat && guideStatus !== "ready")}
      onContextChange={(context) => setSettings("context", context)}
      onFollowupsVisibilityChange={setShowFollowups}
      onSubmit={handleSubmit}
      onStop={handleStop}
    />
  );
  const demoNotice = demoMode ? (
    <div
      className={cn(
        "text-muted-foreground/67 w-full text-center text-xs",
        showGuidedNewChat ? "mt-3" : "translate-y-12",
      )}
    >
      {t.common.notAvailableInDemoMode}
    </div>
  ) : null;

  return (
    <ThreadContext.Provider value={{ thread }}>
      <ChatBox threadId={threadId}>
        <div className="relative flex size-full min-h-0 justify-between">
          <header
            className={cn(
              "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center gap-2 px-4",
              isNewThread ? "bg-transparent" : "ws-topbar",
            )}
          >
            {/* Agent badge */}
            <div className="flex shrink-0 items-center gap-1.5 rounded-md border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-1">
              <BotIcon className="h-3.5 w-3.5 text-indigo-600 dark:text-indigo-400" />
              <span className="text-[10.5px] font-semibold text-indigo-600 dark:text-indigo-400">
                {guideDisplayName ?? agent?.name ?? agent_name}
              </span>
            </div>

            <div className="flex w-full items-center text-sm font-medium">
              <ThreadTitle threadId={threadId} thread={thread} />
            </div>
            <div className="mr-4 flex items-center">
              <Tooltip content={t.agents.newChat}>
                <Button
                  size="sm"
                  className="text-muted-foreground hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:text-indigo-400"
                  variant="ghost"
                  onClick={() => {
                    router.push(`/workspace/agents/${agent_name}/chats/new`);
                  }}
                >
                  <PlusSquare /> {t.agents.newChat}
                </Button>
              </Tooltip>
              <TokenUsageIndicator
                enabled={tokenUsageEnabled}
                messages={thread.messages}
                warnThresholdTokens={
                  tokenBudgetWarnTokens[agent_name] ??
                  tokenBudgetWarnTokens.default
                }
                threadUsage={threadTokenUsage}
              />
              <ExportTrigger threadId={threadId} />
              <ArtifactTrigger />
            </div>
          </header>

          <main className="flex min-h-0 max-w-full grow flex-col">
            {showGuidedNewChat && guideAgentName ? (
              <div className="size-full overflow-y-auto pt-12">
                <AgentNewChatGuide
                  agentName={guideAgentName}
                  handoffReady={handoffReady}
                  handoffKey={handoffKey}
                  status={guideStatus}
                  disabled={demoMode}
                  onRetry={() => void refetchAgent()}
                >
                  {inputBox}
                  {demoNotice}
                </AgentNewChatGuide>
              </div>
            ) : (
              <>
                <div className="flex size-full justify-center">
                  <MessageList
                    className={cn("size-full", !isNewThread && "pt-10")}
                    threadId={threadId}
                    thread={thread}
                    paddingBottom={messageListPaddingBottom}
                    tokenUsageEnabled={tokenUsageEnabled}
                    hasMoreHistory={hasMoreHistory}
                    loadMoreHistory={loadMoreHistory}
                    isHistoryLoading={isHistoryLoading}
                  />
                </div>

                <div className="absolute right-0 bottom-0 left-0 z-30 flex justify-center px-4">
                  <div
                    className={cn(
                      "relative w-full",
                      isNewThread && "-translate-y-[calc(50vh-96px)]",
                      isNewThread
                        ? "max-w-(--container-width-sm)"
                        : "max-w-(--container-width-md)",
                    )}
                  >
                    <div className="absolute -top-4 right-0 left-0 z-0">
                      <div className="absolute right-0 bottom-0 left-0">
                        <TodoList
                          className="bg-background/5"
                          todos={thread.values.todos ?? []}
                          hidden={
                            !thread.values.todos ||
                            thread.values.todos.length === 0
                          }
                        />
                      </div>
                    </div>

                    {inputBox}
                    {demoNotice}
                  </div>
                </div>
              </>
            )}
          </main>
        </div>
      </ChatBox>
    </ThreadContext.Provider>
  );
}
