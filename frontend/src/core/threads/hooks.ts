import type { Message, Run } from "@langchain/langgraph-sdk";
import type { ThreadsClient } from "@langchain/langgraph-sdk/client";
import { useStream } from "@langchain/langgraph-sdk/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";

import { getAPIClient } from "../api";
import { fetch } from "../api/fetcher";
import { getBackendBaseURL } from "../config";
import { useI18n } from "../i18n/hooks";
import { isHiddenFromUIMessage } from "../messages/utils";
import type { FileInMessage } from "../messages/utils";
import type { LocalSettings } from "../settings";
import { useUpdateSubtask } from "../tasks/context";
import { mapTaskEvent } from "../tasks/task-event";
import type { UploadedFileInfo } from "../uploads";
import { promptInputFilePartToFile, uploadFilesWithProgress } from "../uploads";

import { friendlySendErrorMessage } from "./errors";
import type { AgentThread, AgentThreadState, RunMessage } from "./types";
import { THREAD_PINNED_METADATA_KEY } from "./utils";

export type ToolEndEvent = {
  name: string;
  data: unknown;
};

/**
 * Custom 事件 → toast 文案路由（纯函数，tests/unit/core/threads/custom-event-toast.test.ts 钉住）。
 *
 * - llm_retry：LLMErrorHandlingMiddleware 的重试提示（后端中文 message）
 * - llm_empty_retry / llm_empty_fallback：空响应治理事件（B-M1, 2026-09-24 R3）——
 *   重试文案后端已含「第 N/M 次」，fallback 文案即兜底内容提示
 * - token_budget_warn：thread 累计 tokens 超阈值（D3/R10, 2026-09-24，后端每 run 至多发一次）
 *
 * 非白名单 type 或缺/空 message → null（不 toast）。
 */
export function toastableCustomEventMessage(event: unknown): string | null {
  if (typeof event !== "object" || event === null) return null;
  const { type, message } = event as { type?: unknown; message?: unknown };
  if (typeof type !== "string") return null;
  if (
    type !== "llm_retry" &&
    type !== "llm_empty_retry" &&
    type !== "llm_empty_fallback" &&
    type !== "token_budget_warn"
  ) {
    return null;
  }
  if (typeof message !== "string" || !message.trim()) return null;
  return message;
}

export type ThreadStreamOptions = {
  threadId?: string | null | undefined;
  context: LocalSettings["context"];
  onSend?: (threadId: string) => void;
  onStart?: (threadId: string, runId: string) => void;
  onFinish?: (state: AgentThreadState) => void;
  onToolEnd?: (event: ToolEndEvent) => void;
};

type SendMessageOptions = {
  additionalKwargs?: Record<string, unknown>;
};

/** Suffix DynamicContextMiddleware's ID-swap gives the real user message (D-3):
 *  the injected reminder SystemMessage takes the original id ``X`` while the
 *  user copy becomes ``X__user``. Normalizing human-message keys to the base id
 *  keeps a history copy (id=X, e.g. a regenerate strip-and-replay edge case) and
 *  its committed ``__user`` copy resolving to one UI message instead of double
 *  rendering. */
const INJECTED_USER_MESSAGE_ID_SUFFIX = "__user";

/** Merge key for a message: tool_call_id when present, else the id with the
 *  dynamic-context ``__user`` suffix stripped from human messages. */
function messageMergeKey(message: Message): string | undefined {
  if ("tool_call_id" in message && message.tool_call_id) {
    return message.tool_call_id;
  }
  if (message.id) {
    return message.type === "human" &&
      message.id.endsWith(INJECTED_USER_MESSAGE_ID_SUFFIX)
      ? message.id.slice(0, -INJECTED_USER_MESSAGE_ID_SUFFIX.length) ||
          message.id
      : message.id;
  }
  return undefined;
}

export function mergeMessages(
  historyMessages: Message[],
  threadMessages: Message[],
  optimisticMessages: Message[],
): Message[] {
  // Only VISIBLE live messages may replace their history copies: hidden live
  // messages are dropped by groupMessages at display time, so letting them
  // trim history would make the message vanish from the UI entirely
  // (semantics locked by message-merge.test.ts).
  const visibleThreadIds = new Set(
    threadMessages
      .filter((m) => !isHiddenFromUIMessage(m))
      .map((m) => messageMergeKey(m))
      .filter(Boolean),
  );

  // Id-set subtraction, not contiguous-suffix scanning: hidden/middleware
  // messages inside history no longer break overlap detection — they survive
  // the filter and are hidden at display time by groupMessages. A gap in the
  // overlap (regenerated LangGraph ids) only affects that single message
  // instead of re-prepending the whole history (2026-07-24 reorder bug).
  const dedupedHistory = historyMessages.filter((msg) => {
    const key = messageMergeKey(msg);
    return !key || !visibleThreadIds.has(key);
  });

  return [...dedupedHistory, ...threadMessages, ...optimisticMessages];
}

function getStreamErrorMessage(error: unknown): string {
  if (typeof error === "string" && error.trim()) {
    return error;
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  if (typeof error === "object" && error !== null) {
    const message = Reflect.get(error, "message");
    if (typeof message === "string" && message.trim()) {
      return message;
    }
    const nestedError = Reflect.get(error, "error");
    if (nestedError instanceof Error && nestedError.message.trim()) {
      return nestedError.message;
    }
    if (typeof nestedError === "string" && nestedError.trim()) {
      return nestedError;
    }
  }
  return "Request failed.";
}

export function useThreadStream({
  threadId,
  context,
  onSend,
  onStart,
  onFinish,
  onToolEnd,
}: ThreadStreamOptions) {
  const { t } = useI18n();
  // Track the thread ID that is currently streaming to handle thread changes during streaming
  const [onStreamThreadId, setOnStreamThreadId] = useState(() => threadId);
  // Ref to track current thread ID across async callbacks without causing re-renders,
  // and to allow access to the current thread id in onUpdateEvent
  const threadIdRef = useRef<string | null>(threadId ?? null);
  const startedRef = useRef(false);
  const listeners = useRef({
    onSend,
    onStart,
    onFinish,
    onToolEnd,
  });

  const {
    messages: history,
    hasMore: hasMoreHistory,
    loadMore: loadMoreHistory,
    loading: isHistoryLoading,
  } = useThreadHistory(onStreamThreadId ?? "");

  // Keep listeners ref updated with latest callbacks
  useEffect(() => {
    listeners.current = { onSend, onStart, onFinish, onToolEnd };
  }, [onSend, onStart, onFinish, onToolEnd]);

  useEffect(() => {
    const normalizedThreadId = threadId ?? null;
    if (!normalizedThreadId) {
      // Reset when the UI moves back to a brand new unsaved thread.
      startedRef.current = false;
      setOnStreamThreadId(normalizedThreadId);
    } else {
      setOnStreamThreadId(normalizedThreadId);
    }
    threadIdRef.current = normalizedThreadId;
  }, [threadId]);

  const handleStreamStart = useCallback((_threadId: string, _runId: string) => {
    threadIdRef.current = _threadId;
    if (!startedRef.current) {
      listeners.current.onStart?.(_threadId, _runId);
      startedRef.current = true;
    }
    setOnStreamThreadId(_threadId);
  }, []);

  const queryClient = useQueryClient();
  const updateSubtask = useUpdateSubtask();

  const thread = useStream<AgentThreadState>({
    client: getAPIClient(),
    assistantId: "lead_agent",
    threadId: onStreamThreadId,
    reconnectOnMount: true,
    fetchStateHistory: { limit: 1 },
    // 合帧同一 macrotask 收到的流事件为一次 React 通知，不引入可能拖慢持续活跃
    // 流的固定 debounce 间隔。只有布尔档安全：SDK 的数值档是 trailing debounce，
    // chunk 连续快于窗口时会饿死 UI 更新。须显式写：SDK 类型标 @default true，
    // 运行时实为 throttle ?? false。（对标上游 DeerFlow hooks.ts:2136-2140）
    throttle: true,
    onCreated(meta) {
      handleStreamStart(meta.thread_id, meta.run_id);
      if (context.agent_name || context.project_type) {
        void getAPIClient()
          .threads.update(meta.thread_id, {
            metadata: {
              ...(context.agent_name ? { agent_name: context.agent_name } : {}),
              ...(context.project_type
                ? { project_type: context.project_type }
                : {}),
            },
          })
          .catch(() => ({}));
      }
    },
    onLangChainEvent(event) {
      if (event.event === "on_tool_end") {
        listeners.current.onToolEnd?.({
          name: event.name,
          data: event.data,
        });
      }
    },
    onUpdateEvent(data) {
      const updates: Array<Partial<AgentThreadState> | null> = Object.values(
        data || {},
      );
      for (const update of updates) {
        if (update && "title" in update && update.title) {
          void queryClient.setQueriesData(
            {
              queryKey: ["threads", "search"],
              exact: false,
            },
            (oldData: Array<AgentThread> | undefined) => {
              return oldData?.map((t) => {
                if (t.thread_id === threadIdRef.current) {
                  return {
                    ...t,
                    values: {
                      ...t.values,
                      title: update.title,
                    },
                  };
                }
                return t;
              });
            },
          );
        }
      }
    },
    onCustomEvent(event: unknown) {
      const patch = mapTaskEvent(event);
      if (patch) {
        updateSubtask(patch);
        return;
      }

      const toastMessage = toastableCustomEventMessage(event);
      if (toastMessage) {
        toast(toastMessage);
      }
    },
    onError(error) {
      setOptimisticMessages([]);
      const errorMsg = getStreamErrorMessage(error);
      // Stale run reconnection: the run no longer exists on the server
      // (restart / multi-worker / cleanup).  Clean up the stored run-id so
      // the next mount doesn't retry, and skip the error toast since the
      // thread history still loads correctly via fetchStateHistory.
      if (errorMsg.includes("not found") || /HTTP\s*404/i.test(errorMsg)) {
        if (typeof window !== "undefined" && onStreamThreadId) {
          window.sessionStorage.removeItem(`lg:stream:${onStreamThreadId}`);
        }
        return;
      }
      // 原始 error 带 status；errorMsg 兜住 SDK 嵌套形态（{error: Error}）里的后端 detail 文案。
      const friendly =
        friendlySendErrorMessage(error) ?? friendlySendErrorMessage(errorMsg);
      if (friendly) {
        toast.error(friendly);
        return; // 不再走默认错误文案
      }
      toast.error(errorMsg);
    },
    onFinish(state) {
      listeners.current.onFinish?.(state.values);
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      // P2-15：run 结束后刷新当前挂载的 artifact。artifact 可能在本次 run 里被改写，
      // 而 useArtifactContent 的 staleTime(5s) 是有意的抗陈旧设计（不动它）；这里主动
      // invalidate 触发挂载中的查询重拉，免去用户手动切走切回才看到最新内容。
      void queryClient.invalidateQueries({
        queryKey: ["artifact"],
        refetchType: "active",
      });
    },
  });

  // Optimistic messages shown before the server stream responds
  const [optimisticMessages, setOptimisticMessages] = useState<Message[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const sendInFlightRef = useRef(false);
  // Track the last server message id so we know when the server tail has
  // advanced (robust to context compression keeping the count unchanged).
  const prevTailIdRef = useRef<string | undefined>(thread.messages.at(-1)?.id);

  // Reset thread-local pending UI state when switching between threads so
  // optimistic messages and in-flight guards do not leak across chat views.
  useEffect(() => {
    startedRef.current = false;
    sendInFlightRef.current = false;
    prevTailIdRef.current = undefined;
  }, [threadId]);

  // Clear optimistic when the server tail advances (robust to context
  // compression that keeps message count unchanged — formerly length-only
  // comparison let optimistic messages stick, 2026-07-24).
  useEffect(() => {
    if (
      shouldClearOptimistic(
        prevTailIdRef.current,
        thread.messages,
        optimisticMessages.length,
      )
    ) {
      setOptimisticMessages([]);
    }
    prevTailIdRef.current = thread.messages.at(-1)?.id;
  }, [thread.messages, optimisticMessages.length]);

  const sendMessage = useCallback(
    async (
      threadId: string,
      message: PromptInputMessage,
      extraContext?: Record<string, unknown>,
      options?: SendMessageOptions,
    ) => {
      if (sendInFlightRef.current) {
        return;
      }
      sendInFlightRef.current = true;

      const text = message.text.trim();

      // Build optimistic files list with uploading status
      const optimisticFiles: FileInMessage[] = (message.files ?? []).map(
        (f) => ({
          filename: f.filename ?? "",
          size: 0,
          status: "uploading" as const,
        }),
      );

      const hideFromUI = options?.additionalKwargs?.hide_from_ui === true;
      const optimisticAdditionalKwargs = {
        ...options?.additionalKwargs,
        ...(optimisticFiles.length > 0 ? { files: optimisticFiles } : {}),
      };

      const newOptimistic: Message[] = [];
      if (!hideFromUI) {
        newOptimistic.push({
          type: "human",
          id: `opt-human-${Date.now()}`,
          content: text ? [{ type: "text", text }] : "",
          additional_kwargs: optimisticAdditionalKwargs,
        });
      }

      if (optimisticFiles.length > 0 && !hideFromUI) {
        // Mock AI message while files are being uploaded
        newOptimistic.push({
          type: "ai",
          id: `opt-ai-${Date.now()}`,
          content: t.uploads.uploadingFiles,
          additional_kwargs: { element: "task" },
        });
      }
      setOptimisticMessages(newOptimistic);

      listeners.current.onSend?.(threadId);

      let uploadedFileInfo: UploadedFileInfo[] = [];

      try {
        // Upload files first if any
        if (message.files && message.files.length > 0) {
          setIsUploading(true);
          setUploadProgress(0);
          try {
            const filePromises = message.files.map((fileUIPart) =>
              promptInputFilePartToFile(fileUIPart),
            );

            const conversionResults = await Promise.all(filePromises);
            const files = conversionResults.filter(
              (file): file is File => file !== null,
            );
            const failedConversions = conversionResults.length - files.length;

            if (failedConversions > 0) {
              throw new Error(
                `Failed to prepare ${failedConversions} attachment(s) for upload. Please retry.`,
              );
            }

            if (!threadId) {
              throw new Error("Thread is not ready for file upload.");
            }

            if (files.length > 0) {
              const uploadResponse = await uploadFilesWithProgress(
                threadId,
                files,
                (loaded, total) => {
                  setUploadProgress(
                    total > 0 ? Math.round((loaded / total) * 100) : 0,
                  );
                },
              );
              uploadedFileInfo = uploadResponse.files;

              // Update optimistic human message with uploaded status + paths
              const uploadedFiles: FileInMessage[] = uploadedFileInfo.map(
                (info) => ({
                  filename: info.filename,
                  size: info.size,
                  path: info.virtual_path,
                  status: "uploaded" as const,
                }),
              );
              setOptimisticMessages((messages) => {
                if (messages.length > 1 && messages[0]) {
                  const humanMessage: Message = messages[0];
                  return [
                    {
                      ...humanMessage,
                      additional_kwargs: { files: uploadedFiles },
                    },
                    ...messages.slice(1),
                  ];
                }
                return messages;
              });
            }
          } catch (error) {
            const errorMessage =
              error instanceof Error
                ? error.message
                : "Failed to upload files.";
            toast.error(errorMessage);
            setOptimisticMessages([]);
            throw error;
          } finally {
            setIsUploading(false);
            setUploadProgress(0);
          }
        }

        // Build files metadata for submission (included in additional_kwargs)
        const filesForSubmit: FileInMessage[] = uploadedFileInfo.map(
          (info) => ({
            filename: info.filename,
            size: info.size,
            path: info.virtual_path,
            status: "uploaded" as const,
          }),
        );

        await thread.submit(
          {
            messages: [
              {
                type: "human",
                content: [
                  {
                    type: "text",
                    text,
                  },
                ],
                additional_kwargs: {
                  ...options?.additionalKwargs,
                  ...(filesForSubmit.length > 0
                    ? { files: filesForSubmit }
                    : {}),
                },
              },
            ],
          },
          {
            threadId: threadId,
            streamSubgraphs: true,
            streamResumable: true,
            config: {
              recursion_limit: 2500,
            },
            context: {
              ...extraContext,
              ...context,
              thinking_enabled: context.mode !== "flash",
              is_plan_mode: context.mode === "pro" || context.mode === "ultra",
              subagent_enabled: context.mode === "ultra",
              reasoning_effort:
                context.reasoning_effort ??
                (context.mode === "ultra"
                  ? "high"
                  : context.mode === "pro"
                    ? "medium"
                    : context.mode === "thinking"
                      ? "low"
                      : undefined),
              thread_id: threadId,
            },
          },
        );
        void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      } catch (error) {
        setOptimisticMessages([]);
        setIsUploading(false);
        throw error;
      } finally {
        sendInFlightRef.current = false;
      }
    },
    [thread, t.uploads.uploadingFiles, context, queryClient],
  );

  // Memoized so the merged array keeps a stable identity between renders that
  // don't actually change messages. Consumers put this array in effect deps
  // (useSyncSubtaskStatus), so rebuilding it every render re-fired those
  // effects continuously — one link of the chat render loop (React #185).
  const mergedMessages = useMemo(
    () => mergeMessages(history, thread.messages, optimisticMessages),
    [history, thread.messages, optimisticMessages],
  );

  // Merge history, live stream, and optimistic messages for display
  // History messages may overlap with thread.messages; thread.messages take precedence
  const mergedThread = useMemo(
    () =>
      ({
        ...thread,
        messages: mergedMessages,
      }) as typeof thread,
    [thread, mergedMessages],
  );

  return {
    thread: mergedThread,
    sendMessage,
    isUploading,
    uploadProgress,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } as const;
}

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

const HISTORY_PAGE_SIZE = 200;
const HISTORY_MAX_PAGES = 25; // 5000 条/run 安全上限

/**
 * 分页拉全单 run 的历史消息（后端 GET /api/threads/{tid}/runs/{rid}/messages
 * 支持 limit/before_seq/after_seq 游标，返回 {data, has_more}）。
 * 此前只拉首 50 条且从未读分页字段 → 长 run 静默截断、首条提示词丢失
 * （2026-07-24 消息丢失 P0）。middleware caller 消息在此过滤（同旧行为）。
 */
export async function fetchAllRunMessages(
  threadId: string,
  runId: string,
  fetchImpl: FetchLike = fetch,
): Promise<RunMessage[]> {
  const all: RunMessage[] = [];
  let afterSeq: number | undefined;
  for (let pageNum = 0; pageNum < HISTORY_MAX_PAGES; pageNum++) {
    const params = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE) });
    if (afterSeq !== undefined) {
      params.set("after_seq", String(afterSeq));
    }
    const result: { data: RunMessage[]; has_more: boolean } = await fetchImpl(
      `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/messages?${params}`,
      {
        method: "GET",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
      },
    ).then((res) => res.json());
    all.push(...result.data);
    const last = result.data[result.data.length - 1];
    if (!result.has_more || !last) {
      break;
    }
    afterSeq = last.seq;
  }
  return all
    .filter((m) => !m.metadata.caller?.startsWith("middleware:"))
    .map((m) => ({
      run_id: m.run_id,
      seq: m.seq,
      content: m.content,
      metadata: m.metadata,
      created_at: m.created_at,
    }));
}

/**
 * optimistic 清理判定：服务器尾部 id 变化才算回包——context 压缩可能让
 * 消息数不变（旧的 length 比较会让 optimistic 滞留，2026-07-24）。
 */
export function shouldClearOptimistic(
  prevTailId: string | undefined,
  threadMessages: Message[],
  optimisticCount: number,
): boolean {
  if (optimisticCount === 0) {
    return false;
  }
  return threadMessages.at(-1)?.id !== prevTailId;
}

export function useThreadHistory(threadId: string) {
  const runs = useThreadRuns(threadId);
  const threadIdRef = useRef(threadId);
  const runsRef = useRef(runs.data ?? []);
  const indexRef = useRef(-1);
  const loadingRef = useRef(false);
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);

  loadingRef.current = loading;
  const loadMessages = useCallback(async () => {
    if (runsRef.current.length === 0) {
      return;
    }
    const run = runsRef.current[indexRef.current];
    if (!run || loadingRef.current) {
      return;
    }
    try {
      setLoading(true);
      const all = await fetchAllRunMessages(threadIdRef.current, run.run_id);
      const _messages = all.map((m) => m.content);
      setMessages((prev) => [..._messages, ...prev]);
      indexRef.current -= 1;
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    threadIdRef.current = threadId;
    if (runs.data && runs.data.length > 0) {
      runsRef.current = runs.data ?? [];
      indexRef.current = runs.data.length - 1;
    }
    loadMessages().catch(() => {
      toast.error("Failed to load thread history.");
    });
  }, [threadId, runs.data, loadMessages]);

  const hasMore = indexRef.current >= 0 || !runs.data;
  return {
    runs: runs.data,
    messages,
    loading,
    hasMore,
    loadMore: loadMessages,
  };
}

export function useThreads(
  params: Parameters<ThreadsClient["search"]>[0] = {
    limit: 50,
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "updated_at", "values", "metadata"],
  },
) {
  const apiClient = getAPIClient();
  return useQuery<AgentThread[]>({
    queryKey: ["threads", "search", params],
    queryFn: async () => {
      const maxResults = params.limit;
      const initialOffset = params.offset ?? 0;
      const DEFAULT_PAGE_SIZE = 50;

      // Preserve prior semantics: if a non-positive limit is explicitly provided,
      // delegate to a single search call with the original parameters.
      if (maxResults !== undefined && maxResults <= 0) {
        const response =
          await apiClient.threads.search<AgentThreadState>(params);
        return response as AgentThread[];
      }

      const pageSize =
        typeof maxResults === "number" && maxResults > 0
          ? Math.min(DEFAULT_PAGE_SIZE, maxResults)
          : DEFAULT_PAGE_SIZE;

      const threads: AgentThread[] = [];
      let offset = initialOffset;

      while (true) {
        if (typeof maxResults === "number" && threads.length >= maxResults) {
          break;
        }

        const currentLimit =
          typeof maxResults === "number"
            ? Math.min(pageSize, maxResults - threads.length)
            : pageSize;

        if (typeof maxResults === "number" && currentLimit <= 0) {
          break;
        }

        const response = (await apiClient.threads.search<AgentThreadState>({
          ...params,
          limit: currentLimit,
          offset,
        })) as AgentThread[];

        threads.push(...response);

        if (response.length < currentLimit) {
          break;
        }

        offset += response.length;
      }

      // Filter out writer-from-scratch threads from chat list
      return threads.filter(
        (t) => t.metadata?.source !== "writer-from-scratch",
      );
    },
    refetchOnWindowFocus: false,
    // P2-8：线程列表每 5s 轮询，标题/排序(updated_at)及时刷新。函数式 interval + 可见性
    // 判断：后台标签页/失焦时停轮询（避免不可见时无意义打 + 流式期间 updated_at 变化致
    // 侧栏每 5s 重排、悬停项移位），回前台由 TanStack 自动恢复（B3）。
    // 2026-10-06 性能批：再加路由门控——仅 chats 相关页轮询（普通会话 /workspace/chats
    // 与 agent 会话 /workspace/agents/*/chats）；gallery/assets/console/video-gen 等
    // 稳态页停 5s 打点；回到 chats 由组件挂载重取承接（refetchOnMount——窗口聚焦
    // 不重取，本查询 refetchOnWindowFocus:false）。interval 函数每跳
    // 重估，路由切换后下一跳即生效。
    refetchInterval: () => {
      if (
        typeof document === "undefined" ||
        document.visibilityState !== "visible"
      )
        return false;
      const path =
        typeof window !== "undefined" ? window.location.pathname : "";
      return path.includes("/chats") ? 5_000 : false;
    },
  });
}

export function useThreadRuns(threadId?: string) {
  const apiClient = getAPIClient();
  return useQuery<Run[]>({
    queryKey: ["thread", threadId],
    queryFn: async () => {
      if (!threadId) {
        return [];
      }
      const response = await apiClient.runs.list(threadId);
      return response;
    },
    refetchOnWindowFocus: false,
  });
}

export function useDeleteThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({ threadId }: { threadId: string }) => {
      await apiClient.threads.delete(threadId);

      const response = await fetch(
        `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}`,
        {
          method: "DELETE",
        },
      );

      if (!response.ok) {
        const error = await response
          .json()
          .catch(() => ({ detail: "Failed to delete local thread data." }));
        throw new Error(error.detail ?? "Failed to delete local thread data.");
      }
    },
    onSuccess(_, { threadId }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread> | undefined) => {
          if (oldData == null) {
            return oldData;
          }
          return oldData.filter((t) => t.thread_id !== threadId);
        },
      );
      // B4-3：清理该线程的输入草稿，否则 localStorage 随删除的线程无限堆积。
      try {
        window.localStorage.removeItem(`draft:${threadId}`);
      } catch {
        // 隐私模式等不可写——忽略。
      }
    },
    onSettled() {
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
    },
  });
}

export function useRenameThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      title,
    }: {
      threadId: string;
      title: string;
    }) => {
      await apiClient.threads.updateState(threadId, {
        values: { title },
      });
    },
    onSuccess(_, { threadId, title }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread>) => {
          return oldData.map((t) => {
            if (t.thread_id === threadId) {
              return {
                ...t,
                values: {
                  ...t.values,
                  title,
                },
              };
            }
            return t;
          });
        },
      );
    },
  });
}

/**
 * Pin/unpin a thread by PATCHing the `deerflow_pinned` metadata key.
 *
 * Additive hook (P2-9): uses the CSRF-aware fetch wrapper. The backend
 * treats a pin-only PATCH as no-touch, so pinning never bumps updated_at.
 */
export function usePinThread() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      pinned,
    }: {
      threadId: string;
      pinned: boolean;
    }) => {
      const response = await fetch(
        `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            metadata: { [THREAD_PINNED_METADATA_KEY]: pinned },
          }),
        },
      );
      if (!response.ok) {
        const error = await response
          .json()
          .catch(() => ({ detail: "Failed to update conversation." }));
        throw new Error(error.detail ?? "Failed to update conversation.");
      }
      return (await response.json()) as {
        metadata?: Record<string, unknown>;
      };
    },
    onSuccess(response, { threadId, pinned }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread> | undefined) => {
          if (oldData == null) {
            return oldData;
          }
          return oldData.map((t) => {
            if (t.thread_id === threadId) {
              return {
                ...t,
                metadata: {
                  ...(response.metadata ?? {}),
                  [THREAD_PINNED_METADATA_KEY]: pinned,
                },
              };
            }
            return t;
          });
        },
      );
    },
    onSettled() {
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
    },
  });
}
