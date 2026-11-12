"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { type PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { usePromptInputController } from "@/components/ai-elements/prompt-input";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import {
  ChatBox,
  useSpecificChatMode,
  useThreadChat,
} from "@/components/workspace/chats";
import { ExportTrigger } from "@/components/workspace/export-trigger";
import { InputBox } from "@/components/workspace/input-box";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { Welcome } from "@/components/workspace/welcome";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { useNotification } from "@/core/notification/hooks";
import { useThreadSettings } from "@/core/settings";
import { shouldAutoSend } from "@/core/threads/autosend";
import { useThreadStream } from "@/core/threads/hooks";
import { useThreadTokenUsage } from "@/core/threads/token-usage";
import { textOfMessage } from "@/core/threads/utils";
import { env } from "@/env";
import { PENDING_PROMPT_KEY } from "@/lib/pending-prompt";
import { cn } from "@/lib/utils";

export default function ChatPage() {
  const { t } = useI18n();
  const { threadId, setThreadId, isNewThread, setIsNewThread } =
    useThreadChat();
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
  const mountedRef = useRef(false);
  useSpecificChatMode();

  useEffect(() => {
    mountedRef.current = true;
  }, []);

  const { showNotification } = useNotification();

  const {
    thread,
    sendMessage,
    isUploading,
    uploadProgress,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    context: settings.context,
    onSend: (_threadId) => {
      setThreadId(_threadId);
      setIsNewThread(false);
    },
    onStart: (createdThreadId) => {
      setThreadId(createdThreadId);
      setIsNewThread(false);
      // ! Important: Never use next.js router for navigation in this case, otherwise it will cause the thread to re-mount and lose all states. Use native history API instead.
      history.replaceState(null, "", `/workspace/chats/${createdThreadId}`);
    },
    onFinish: (state) => {
      setTokenUsageRefresh((n) => n + 1);
      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages.at(-1);
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

  // 方案 A: gallery「制作同款」跳转后 auto-send。
  // prompt 一律走 sessionStorage JSON（SEC-H11：URL ?msg=&autosend=1 一键
  // 自动发送通道已移除，书签/分享链接最多预填不发送）。
  // 不改全局 prompt-input.tsx——auto-send 逻辑只在这个 page 内，
  // 幂等靠 autoSendRanRef + sendMessage 内置的 sendInFlightRef 双 guard。
  //
  // 2026-07-27 文件类灵感两步流：sessionStorage 里 autoSend:false 的 pending
  // prompt 只预填输入框、不发送（用户用回形针上传文件后手动发）。
  // ?msg= 无 autosend 的短 prompt 预填由 layout 的 initialInput 负责；
  // 这里只补 sessionStorage 长 prompt 路径。readPendingPrompt 无法表达 autoSend
  // 分支（它只回传 text），故保留手写解析、复用 PENDING_PROMPT_KEY 常量。
  const autoSendRanRef = useRef(false);
  const { textInput } = usePromptInputController();
  const prefillRunRef = useRef<string | null>(null);
  useEffect(() => {
    if (autoSendRanRef.current) return;

    let text = "";
    let autosend = false;
    let fromStorage = false;
    let allowExistingThread = false;

    // 唯一 prompt 路径：sessionStorage JSON {prompt, autoSend, existingThread}
    try {
      const raw = sessionStorage.getItem(PENDING_PROMPT_KEY);
      if (raw) {
        sessionStorage.removeItem(PENDING_PROMPT_KEY);
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed.prompt === "string") {
          text = parsed.prompt;
          fromStorage = true;
          if (parsed.autoSend === true) autosend = true;
          if (parsed.existingThread === true) allowExistingThread = true;
        }
      }
    } catch {
      // noop — 损坏的 sessionStorage 项忽略
    }

    if (
      shouldAutoSend({
        autosend,
        text,
        isNewThread,
        allowExistingThread,
      })
    ) {
      autoSendRanRef.current = true;
      void sendMessage(threadId, { text, files: [] });
      return;
    }
    // 预填分支（两步流）：sessionStorage 长 prompt 且不要求 autoSend →
    // 填入输入框等用户上传文件后手动发送。无 isNewThread 守卫——defer
    // instantiate 落地的是已存在的空线程（isNewThread=false）也要预填。
    if (fromStorage && text && prefillRunRef.current !== text) {
      prefillRunRef.current = text;
      textInput.setInput(text);
    }
    // textInput.setInput 是稳定的 useState setter——直接调用，不依赖 textInput
    // （controller 对象每 render 新建，依赖它会每次渲染重跑）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isNewThread, threadId, sendMessage]);

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      void sendMessage(threadId, message);
    },
    [sendMessage, threadId],
  );
  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);

  return (
    <ThreadContext.Provider value={{ thread }}>
      <ChatBox threadId={threadId}>
        <div className="relative flex size-full min-h-0 justify-between">
          <header
            className={cn(
              "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center px-4",
              isNewThread ? "bg-transparent" : "ws-topbar",
            )}
          >
            <div className="flex w-full items-center text-sm font-medium">
              <ThreadTitle threadId={threadId} thread={thread} />
            </div>
            <div className="flex items-center gap-2">
              <TokenUsageIndicator
                enabled={tokenUsageEnabled}
                messages={thread.messages}
                // D3：通用聊天页无 agent 上下文，用 default 告警线。
                warnThresholdTokens={tokenBudgetWarnTokens.default}
                threadUsage={threadTokenUsage}
              />
              <ExportTrigger threadId={threadId} />
              <ArtifactTrigger />
            </div>
          </header>
          <main className="flex min-h-0 max-w-full grow flex-col">
            <div className="flex size-full justify-center">
              <MessageList
                className={cn("size-full", !isNewThread && "pt-10")}
                threadId={threadId}
                thread={thread}
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
                        !thread.values.todos || thread.values.todos.length === 0
                      }
                    />
                  </div>
                </div>
                {mountedRef.current ? (
                  <InputBox
                    className={cn("bg-background/5 w-full -translate-y-4")}
                    isNewThread={isNewThread}
                    threadId={threadId}
                    autoFocus={isNewThread}
                    status={
                      thread.error
                        ? "error"
                        : thread.isLoading
                          ? "streaming"
                          : "ready"
                    }
                    context={settings.context}
                    extraHeader={
                      isNewThread && <Welcome mode={settings.context.mode} />
                    }
                    isUploading={isUploading}
                    uploadProgress={uploadProgress}
                    onContextChange={(context) =>
                      setSettings("context", context)
                    }
                    onSubmit={handleSubmit}
                    onStop={handleStop}
                  />
                ) : (
                  <div
                    aria-hidden="true"
                    className={cn(
                      "bg-background/5 h-32 w-full -translate-y-4 rounded-2xl opacity-0",
                    )}
                  />
                )}
                {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" && (
                  <div className="text-muted-foreground/67 w-full translate-y-12 text-center text-xs">
                    {t.common.notAvailableInDemoMode}
                  </div>
                )}
              </div>
            </div>
          </main>
        </div>
      </ChatBox>
    </ThreadContext.Provider>
  );
}
