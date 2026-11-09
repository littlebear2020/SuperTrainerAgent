import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { ChevronUpIcon, Loader2Icon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Conversation,
  ConversationContent,
} from "@/components/ai-elements/conversation";
import { Button } from "@/components/ui/button";
import { loadGallerySettings } from "@/core/gallery/api";
import { useI18n } from "@/core/i18n/hooks";
import {
  extractContentFromMessage,
  extractPresentFilesFromMessage,
  extractStoryboardTaskId,
  getMessageGroups,
  hasContent,
  hasPresentFiles,
  hasReasoning,
  hasToolCalls,
  reuseMessageGroups,
  type ThreadMessageGroup,
} from "@/core/messages/utils";
import { useSyncSubtaskStatus } from "@/core/tasks/subtask-status-sync";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { ArtifactFileList } from "../artifacts/artifact-file-list";
import { StreamingIndicator } from "../streaming-indicator";

import { MarkdownContent } from "./markdown-content";
import { MessageGroup } from "./message-group";
import { MessageListItem } from "./message-list-item";
import { MessageTokenUsageList } from "./message-token-usage";
import { MessageListSkeleton } from "./skeleton";
import { StoryboardConfirmCard } from "./storyboard-confirm-card";
import { SubtaskCard } from "./subtask-card";
import { VirtualMessageList } from "./virtual-message-list";

export const MESSAGE_LIST_DEFAULT_PADDING_BOTTOM = 160;
export const MESSAGE_LIST_FOLLOWUPS_EXTRA_PADDING_BOTTOM = 80;

const LOAD_MORE_HISTORY_THROTTLE_MS = 1200;

function LoadMoreHistoryIndicator({
  isLoading,
  hasMore,
  loadMore,
}: {
  isLoading?: boolean;
  hasMore?: boolean;
  loadMore?: () => void;
}) {
  const { t } = useI18n();
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLoadRef = useRef(0);

  const throttledLoadMore = useCallback(() => {
    if (!hasMore || isLoading) {
      return;
    }

    const now = Date.now();
    const remaining =
      LOAD_MORE_HISTORY_THROTTLE_MS - (now - lastLoadRef.current);

    if (remaining <= 0) {
      lastLoadRef.current = now;
      loadMore?.();
      return;
    }

    if (timeoutRef.current) {
      return;
    }

    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      if (!hasMore || isLoading) {
        return;
      }
      lastLoadRef.current = Date.now();
      loadMore?.();
    }, remaining);
  }, [hasMore, isLoading, loadMore]);

  useEffect(() => {
    const element = sentinelRef.current;
    if (!element || !hasMore) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          throttledLoadMore();
        }
      },
      {
        rootMargin: "120px 0px 0px 0px",
      },
    );

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [hasMore, throttledLoadMore]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  if (!hasMore && !isLoading) {
    return null;
  }

  return (
    <div ref={sentinelRef} className="flex w-full justify-center">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="text-muted-foreground hover:text-foreground rounded-full px-3"
        disabled={(isLoading ?? false) || !hasMore}
        onClick={throttledLoadMore}
      >
        {isLoading ? (
          <>
            <Loader2Icon className="mr-2 size-4 animate-spin" />
            {t.common.loading}
          </>
        ) : (
          <>
            <ChevronUpIcon className="mr-2 size-4" />
            {t.common.loadMore}
          </>
        )}
      </Button>
    </div>
  );
}

export function MessageList({
  className,
  threadId,
  thread,
  paddingBottom = MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
  tokenUsageEnabled = false,
  hasMoreHistory,
  loadMoreHistory,
  isHistoryLoading,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  paddingBottom?: number;
  tokenUsageEnabled?: boolean;
  hasMoreHistory?: boolean;
  loadMoreHistory?: () => void;
  isHistoryLoading?: boolean;
}) {
  const { t } = useI18n();
  const messages = thread.messages;
  // Derive subtask status from messages (post-render) + streaming custom events.
  // Replaces the old in-render updateSubtask churn that mutated state during render.
  useSyncSubtaskStatus(messages);

  const [galleryEnabled, setGalleryEnabled] = useState(false);

  useEffect(() => {
    let active = true;
    loadGallerySettings()
      .then((settings) => {
        if (active) {
          setGalleryEnabled(settings.ugc_enabled);
        }
      })
      .catch((err) => {
        console.error("Failed to load gallery settings in MessageList:", err);
      });
    return () => {
      active = false;
    };
  }, []);

  // P1-5：分组复用——内容未变的组沿用上次的 group 对象引用，使 MessageGroup 的 memo
  // 能跳过未变组。流式期末组由 reuseMessageGroups 强制换新引用（SDK 原地 mutate 追加
  // token 必须重渲染），其余组复用旧引用 → memo 命中跳过。prevGroupsRef 跨渲染记忆。
  const prevGroupsRef = useRef<ThreadMessageGroup[]>([]);
  // B1：isLoading 翻转帧（run 开始/结束/stop/error/reconnect）必须让所有组换新引用——
  // 否则这些结束路径保持同一 messages 对象 → reuseMessageGroups 全部复用 → memo（只比
  // 引用）跳过重渲染 → MessageGroup 的 isLoading 滞留（如 ClarificationOptions 卡在
  // disabled）。只在翻转那一帧跳过复用（一次全量重渲染），不逐帧强制（那会触发 FlipDisplay
  // 的 Radix 测量死循环，正是合帧接线崩溃的同类问题）。
  const prevLoadingRef = useRef(thread.isLoading);
  // 2026-10-06 性能批：消息对象 → 前面最近一条 human 消息文本的 O(n) 预建映射
  // （messages 引用变化时重算一次；语义同修前 indexOf 反扫——逐条累积最近前驱）。
  const promptByMsg = useMemo(() => {
    const map = new WeakMap<object, string>();
    let lastHuman = "";
    for (const m of messages) {
      if (m?.type === "human") {
        lastHuman = extractContentFromMessage(m) || "";
      } else {
        map.set(m, lastHuman);
      }
    }
    return map;
  }, [messages]);
  const groups = useMemo(() => {
    const next = getMessageGroups(messages);
    const loadingFlipped = prevLoadingRef.current !== thread.isLoading;
    prevLoadingRef.current = thread.isLoading;
    const reused = loadingFlipped
      ? next
      : reuseMessageGroups(prevGroupsRef.current, next, thread.isLoading);
    prevGroupsRef.current = reused;
    return reused;
  }, [messages, thread.isLoading]);

  // PR-2 4.3：groups.map 整段收敛为 renderGroup 回调，接入 VirtualMessageList
  // （阈值 ≥60 组自动虚拟化，短会话走静态分支——渲染面与修前等价）。
  const renderGroup = useCallback(
    (group: (typeof groups)[number]): React.ReactNode => {
      if (group.type === "human" || group.type === "assistant") {
        return group.messages.map((msg) => {
          // 2026-10-06 性能批：修前每条消息 indexOf 反扫（O(n²)/渲染，
          // 长会话流式期帧率杀手）→ O(n) 预建映射（见 promptByMsg）。
          const promptText =
            msg.type !== "human" ? (promptByMsg.get(msg) ?? "") : undefined;
          return (
            <MessageListItem
              key={`${group.id}/${msg.id}`}
              threadId={threadId}
              message={msg}
              isLoading={thread.isLoading}
              tokenUsageEnabled={tokenUsageEnabled}
              promptText={promptText}
              galleryEnabled={galleryEnabled}
            />
          );
        });
      } else if (group.type === "assistant:clarification") {
        const message = group.messages[0];
        if (message && hasContent(message)) {
          return (
            <div key={group.id} className="w-full">
              <MarkdownContent
                content={extractContentFromMessage(message)}
                isLoading={thread.isLoading}
              />
              <MessageTokenUsageList
                enabled={tokenUsageEnabled}
                isLoading={thread.isLoading}
                messages={group.messages}
              />
            </div>
          );
        }
        return null;
      } else if (group.type === "assistant:present-files") {
        const files: string[] = [];
        for (const message of group.messages) {
          if (hasPresentFiles(message)) {
            const presentFiles = extractPresentFilesFromMessage(message);
            files.push(...presentFiles);
          }
        }
        return (
          <div className="w-full" key={group.id}>
            {group.messages[0] && hasContent(group.messages[0]) && (
              <MarkdownContent
                content={extractContentFromMessage(group.messages[0])}
                isLoading={thread.isLoading}
                className="mb-4"
              />
            )}
            <ArtifactFileList files={files} threadId={threadId} />
            <MessageTokenUsageList
              enabled={tokenUsageEnabled}
              isLoading={thread.isLoading}
              messages={group.messages}
            />
          </div>
        );
      } else if (group.type === "assistant:subagent") {
        // Subtask status is derived in useSyncSubtaskStatus (post-render
        // useEffect, via parseSubtaskResult) and from onCustomEvent during
        // streaming — never mutated during render. Here we only collect the
        // task ids to render their cards; SubtaskCard reads the store.
        const taskIds: string[] = [];
        for (const message of group.messages) {
          if (message.type === "ai") {
            for (const toolCall of message.tool_calls ?? []) {
              if (toolCall.name === "task" && toolCall.id) {
                taskIds.push(toolCall.id);
              }
            }
          }
        }
        const results: React.ReactNode[] = [];
        for (const message of group.messages.filter(
          (message) => message.type === "ai",
        )) {
          if (hasReasoning(message)) {
            results.push(
              <MessageGroup
                key={"thinking-group-" + message.id}
                messages={[message]}
                isLoading={thread.isLoading}
              />,
            );
          }
        }
        // 计数行与卡片循环必须在 per-message 循环外——否则 subagent 组有多条
        // AI 消息时同一 SubtaskCard 被重复 push（同 key 重复渲染）。
        results.push(
          <div
            key="subtask-count"
            className="text-muted-foreground font-norma pt-2 text-sm"
          >
            {t.subtasks.executing(taskIds.length)}
          </div>,
        );
        for (const taskId of taskIds) {
          results.push(
            <SubtaskCard
              key={"task-group-" + taskId}
              taskId={taskId}
              isLoading={thread.isLoading}
            />,
          );
        }
        return (
          <div
            key={"subtask-group-" + group.id}
            className="relative z-1 flex flex-col gap-2"
          >
            {results}
            <MessageTokenUsageList
              enabled={tokenUsageEnabled}
              isLoading={thread.isLoading}
              messages={group.messages}
            />
          </div>
        );
      } else if (group.type === "assistant:storyboard") {
        // plan_storyboard produced a draft blueprint — render a confirmation
        // card (frames table + confirm-render). taskId comes from the
        // tool_result; until it lands mid-stream the card is hidden.
        const taskId = extractStoryboardTaskId(group.messages);
        return (
          <div key={"storyboard-" + group.id} className="w-full">
            {group.messages[0] && hasContent(group.messages[0]) && (
              <MarkdownContent
                content={extractContentFromMessage(group.messages[0])}
                isLoading={thread.isLoading}
                className="mb-4"
              />
            )}
            {taskId && <StoryboardConfirmCard taskId={taskId} />}
            <MessageTokenUsageList
              enabled={tokenUsageEnabled}
              isLoading={thread.isLoading}
              messages={group.messages}
            />
          </div>
        );
      }
      const tokenUsageMessages = group.messages.filter(
        (message) =>
          message.type === "ai" &&
          (hasToolCalls(message) ? true : !hasContent(message)),
      );
      return (
        <div key={"group-" + group.id} className="w-full">
          <MessageGroup
            messages={group.messages}
            isLoading={thread.isLoading}
          />
          <MessageTokenUsageList
            enabled={tokenUsageEnabled}
            isLoading={thread.isLoading}
            messages={tokenUsageMessages}
          />
        </div>
      );
    },
    [
      galleryEnabled,
      promptByMsg,
      t,
      thread.isLoading,
      threadId,
      tokenUsageEnabled,
    ],
  );

  if (thread.isThreadLoading && messages.length === 0) {
    return <MessageListSkeleton />;
  }
  return (
    <Conversation
      className={cn("flex size-full flex-col justify-center", className)}
    >
      <ConversationContent className="mx-auto w-full max-w-(--container-width-md) gap-8 pt-8">
        <LoadMoreHistoryIndicator
          isLoading={isHistoryLoading}
          hasMore={hasMoreHistory}
          loadMore={loadMoreHistory}
        />
        <VirtualMessageList
          groups={groups}
          isLoading={thread.isLoading}
          renderGroup={renderGroup}
        />
        {thread.isLoading && <StreamingIndicator className="my-4" />}
        <div style={{ height: `${paddingBottom}px` }} />
      </ConversationContent>
    </Conversation>
  );
}
