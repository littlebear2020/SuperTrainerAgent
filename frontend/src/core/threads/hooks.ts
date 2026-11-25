import type { Message, Run } from "@langchain/langgraph-sdk";
import type { ThreadsClient } from "@langchain/langgraph-sdk/client";
import { useStream } from "@langchain/langgraph-sdk/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";

import { getAPIClient } from "../api";
import { type CancelOutcome, cancelRun } from "../api/api-client";
import { fetch } from "../api/fetcher";
import { getBackendBaseURL } from "../config";
import { STREAM_REJOIN_KEY_PREFIX } from "../gallery/stream-rejoin";
import { useI18n } from "../i18n/hooks";
import { isHiddenFromUIMessage } from "../messages/utils";
import type { FileInMessage } from "../messages/utils";
import { useModels } from "../models/hooks";
import { normalizeMode } from "../models/modes";
import type { LocalSettings } from "../settings";
import { useUpdateSubtask } from "../tasks/context";
import { mapTaskEvent } from "../tasks/task-event";
import type { UploadedFileInfo } from "../uploads";
import { promptInputFilePartToFile, uploadFilesWithProgress } from "../uploads";

import {
  friendlySendErrorMessage,
  friendlyUploadErrorMessage,
  SEND_BUSY_MESSAGE,
  SEND_NOT_ACCEPTED_MESSAGE,
  SEND_STOPPED_MESSAGE,
} from "./errors";
import {
  CANCEL_WAIT_LIMIT_MS,
  classifyStreamError,
  isRecoverableDisconnect,
  isTerminalRunStatus,
  withTimeout,
} from "./recovery";
import {
  createSubmission,
  SendRejectedError,
  type SendRejectionKind,
  type Submission,
  type SubmissionOutcome,
} from "./submission";
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
  /** 上传完成、提交 run 前：页面只用预生成 UUID 更新地址栏，不提前翻 controlled threadId。 */
  onSend?: (threadId: string) => void;
  /** SDK onCreated：线程与 run 已创建，可以翻 controlled threadId / isNewThread。 */
  onStart?: (threadId: string, runId: string) => void;
  /** 本次 onSend 已触发、onCreated 前失败或停止（PR2）：URL 回退由页面决定。 */
  onSendError?: (threadId: string, opts: { wasNewThread: boolean }) => void;
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

function abortError(): Error {
  const error = new Error("The operation was aborted");
  error.name = "AbortError";
  return error;
}

/**
 * PR2：让「附件转换 / 上传」在取消信号触发时立即拒绝——即使底层实现没有兑现
 * signal，sendMessage 也能按 submission 及时收尾，不会悬挂到实现自己结束。
 */
function withAbort<T>(signal: AbortSignal, promise: Promise<T>): Promise<T> {
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(abortError());
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}

export function useThreadStream({
  threadId,
  context,
  onSend,
  onStart,
  onSendError,
  onFinish,
  onToolEnd,
}: ThreadStreamOptions) {
  const { t } = useI18n();
  const { models } = useModels();
  // Track the thread ID that is currently streaming to handle thread changes during streaming
  const [onStreamThreadId, setOnStreamThreadId] = useState(() => threadId);
  // Ref to track current thread ID across async callbacks without causing re-renders,
  // and to allow access to the current thread id in onUpdateEvent
  const threadIdRef = useRef<string | null>(threadId ?? null);
  const startedRef = useRef(false);
  const listeners = useRef({
    onSend,
    onStart,
    onSendError,
    onFinish,
    onToolEnd,
  });

  // ── PR2（2026-11-24 前端可靠性批）：提交受理 / 完成双生命周期 ──
  // 纯逻辑在 ./submission.ts，这里只做 SDK 接线与共享副作用的归属门控。
  const submissionSeqRef = useRef(0);
  /** 受理前阶段的 submission，或最近一个尚未完成的；无 meta 的回调归它。 */
  const currentSubmissionRef = useRef<Submission | null>(null);
  /** onCreated 之后带 meta（run_id）的回调按此归属。 */
  const runSubmissionsRef = useRef(new Map<string, Submission>());
  /** 上传所有者：递增 id + AbortController（切线程 / 卸载 / 受理前停止时取消）。 */
  const uploadOpSeqRef = useRef(0);
  const uploadOpRef = useRef<{
    id: number;
    controller: AbortController;
  } | null>(null);
  /** 用户按了停止：SDK 的流收尾时据此把 completion 记为 stopped。 */
  const stopRequestedRef = useRef(false);
  /** 取消请求在途：SDK 流收尾的 effect 不抢先结案 */
  const cancelPendingRef = useRef(false);
  /** 取消失败（含等待超时）：保留可重试的目标，恢复入口的重试即再次调用同一 cancel。 */
  const [cancelFailure, setCancelFailure] = useState<{
    threadId: string;
    runId: string;
  } | null>(null);
  /** 手动重连单飞：按 (threadId, runId) 记在途，SDK 流收尾时清空。 */
  const reconnectInFlightRef = useRef(new Set<string>());
  const [isReconnecting, setIsReconnecting] = useState(false);

  const {
    messages: history,
    hasMore: hasMoreHistory,
    loadMore: loadMoreHistory,
    refetchRun: refetchRunHistory,
    refetchRunIfStale: refetchRunIfStaleHistory,
    messageRunIds,
    runsById,
    loading: isHistoryLoading,
  } = useThreadHistory(onStreamThreadId ?? "");

  // Keep listeners ref updated with latest callbacks
  useEffect(() => {
    listeners.current = { onSend, onStart, onSendError, onFinish, onToolEnd };
  }, [onSend, onStart, onSendError, onFinish, onToolEnd]);

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

  // 当前流所属的 run：渲染期按线程派生（切线程后自然回到 null），不在 effect 里 setState
  const [activeRun, setActiveRun] = useState<{
    threadId: string;
    runId: string;
  } | null>(null);
  const activeRunId =
    activeRun && activeRun.threadId === threadId ? activeRun.runId : null;

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

  // Optimistic messages shown before the server stream responds
  const [optimisticMessages, setOptimisticMessages] = useState<Message[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const sendInFlightRef = useRef(false);

  /** 受理前失败 / 停止：只对本 submission 生效一次；共享副作用按归属门控。 */
  const rejectSubmission = useCallback(
    (
      submission: Submission,
      kind: SendRejectionKind,
      message: string,
      cause?: unknown,
    ) => {
      if (submission.state !== "pending") return;
      if (currentSubmissionRef.current === submission) {
        uploadOpRef.current?.controller.abort();
        uploadOpRef.current = null;
        setOptimisticMessages([]);
        setIsUploading(false);
        setUploadProgress(0);
        sendInFlightRef.current = false;
      }
      submission.reject(kind, message, cause);
      if (submission.onSendFired) {
        listeners.current.onSendError?.(submission.requestedThreadId, {
          wasNewThread: submission.wasNewThread,
        });
      }
    },
    [],
  );

  /** SDK 回调归属：有 meta 按 run_id，无 meta 归当前尚未受理的 submission。 */
  const attributeCallback = useCallback(
    (meta: { run_id?: string } | undefined): Submission | undefined => {
      if (meta?.run_id) return runSubmissionsRef.current.get(meta.run_id);
      const current = currentSubmissionRef.current;
      return current?.state === "pending" ? current : undefined;
    },
    [],
  );

  /** 受理后的终态：completion 只收尾一次，之后同一 run 的迟到回调走既有路径。 */
  const completeSubmission = useCallback(
    (submission: Submission, outcome: SubmissionOutcome) => {
      if (submission.state !== "accepted") return;
      submission.complete(outcome);
      if (submission.runId) runSubmissionsRef.current.delete(submission.runId);
      if (currentSubmissionRef.current === submission) {
        sendInFlightRef.current = false;
      }
    },
    [],
  );

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
      // PR2：只有当前尚未受理的 submission 认领这次创建；受理 = accepted resolve
      const pending = currentSubmissionRef.current;
      if (pending?.state === "pending") {
        pending.runId = meta.run_id;
        pending.threadId = meta.thread_id;
        runSubmissionsRef.current.set(meta.run_id, pending);
        pending.accept();
      }
      setActiveRun({ threadId: meta.thread_id, runId: meta.run_id });
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
    onError(error, meta) {
      // PR2 归属：受理前的失败 reject accepted；受理后的失败收尾 completion；
      // 不属于任何在途 submission（如 mount rejoin 的历史 run）走既有路径。
      const submission = attributeCallback(meta);
      const errorMsg = getStreamErrorMessage(error);
      // 与 thread.submit 同步失败的提示路径一致：409 / busy 优先用友好文案
      const friendly =
        friendlySendErrorMessage(error) ?? friendlySendErrorMessage(errorMsg);
      if (submission?.state === "pending") {
        rejectSubmission(submission, "failed", friendly ?? errorMsg, error);
      } else if (submission?.state === "accepted") {
        if (stopRequestedRef.current) {
          // 用户主动停止后的收尾错误：completion 按 stopped 结尾，不提示
          completeSubmission(submission, { status: "stopped" });
          return;
        }
        completeSubmission(submission, { status: "failed", error });
      }
      // 共享的 optimistic 清理按归属门控：带 run_id 却不属于任何在途 submission 的迟到
      // 回调（旧 run 的错误）不得清掉新一次发送的 optimistic 消息
      const hasPending = currentSubmissionRef.current?.state === "pending";
      if (
        (submission && currentSubmissionRef.current === submission) ||
        (!meta?.run_id && !hasPending)
      ) {
        setOptimisticMessages([]);
      }
      const errorKind = classifyStreamError(error);
      const onCurrentThread =
        !meta?.thread_id || meta.thread_id === threadIdRef.current;
      // Stale run reconnection: the run no longer exists on the server
      // (restart / multi-worker / cleanup).  Clean up the stored run-id so
      // the next mount doesn't retry, and skip the error toast since the
      // thread history still loads correctly via fetchStateHistory.
      if (errorKind === "stale_run") {
        if (typeof window !== "undefined" && onStreamThreadId) {
          window.sessionStorage.removeItem(
            `${STREAM_REJOIN_KEY_PREFIX}${onStreamThreadId}`,
          );
        }
        // 没有可重连的 run：放掉「当前 run」，并失效 runs 与历史分片（PR3）
        if (onCurrentThread) {
          setActiveRun((current) =>
            !meta?.run_id || current?.runId === meta.run_id ? null : current,
          );
          void queryClient
            .invalidateQueries({ queryKey: ["thread", threadIdRef.current] })
            .catch(() => undefined);
        }
        return;
      }
      if (errorKind === "connection" && onCurrentThread) {
        // 断线：runs 列表有界刷新一次——服务端的 run 若已结束，重连入口随之收起，
        // 分片由下方「终态重取」effect 补齐
        void queryClient
          .invalidateQueries({ queryKey: ["thread", threadIdRef.current] })
          .catch(() => undefined);
      }
      // 原始 error 带 status；errorMsg 兜住 SDK 嵌套形态（{error: Error}）里的后端 detail 文案。
      if (friendly) {
        toast.error(friendly);
        return; // 不再走默认错误文案
      }
      toast.error(errorMsg);
    },
    onStop() {
      // SDK thread.stop() 内同步回调：受理前停止 → accepted 以 stopped reject；
      // 此时服务端尚无 runId，SDK 也不会发 cancel。
      stopRequestedRef.current = true;
      const pending = currentSubmissionRef.current;
      if (pending?.state === "pending") {
        rejectSubmission(pending, "stopped", SEND_STOPPED_MESSAGE);
      }
    },
    onFinish(state, meta) {
      // PR2：按 meta.run_id 定位要收尾的 submission（mount 自动 rejoin 不经过 onCreated）
      const submission = attributeCallback(meta);
      if (submission?.state === "accepted") {
        // 用户按了停止但 SDK 以 onFinish（而非 AbortError / onError）收尾：仍记 stopped，
        // 与流收尾 effect 的判定一致
        completeSubmission(submission, {
          status: stopRequestedRef.current ? "stopped" : "finished",
        });
      }
      listeners.current.onFinish?.(state.values);
      // PR3：强制重取 meta.run_id 指向的分片（补齐流式期间追加的消息）。mount 自动
      // rejoin 不经过 onCreated，不能依赖只由 onStart 设置的 runId；分片层还有
      // generation / epoch 兜底，切线程后的迟到 onFinish 不会写进新线程。
      const finishedRunId = meta?.run_id;
      if (finishedRunId && meta?.thread_id === threadIdRef.current) {
        void queryClient
          .invalidateQueries({ queryKey: ["thread", threadIdRef.current] })
          .then(() => refetchRunHistory(finishedRunId))
          .catch((error: unknown) => {
            console.error("Failed to refetch finished run history", error);
          });
      }
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

  // Track the last server message id so we know when the server tail has
  // advanced (robust to context compression keeping the count unchanged).
  const prevTailIdRef = useRef<string | undefined>(thread.messages.at(-1)?.id);

  // busy 判定读最新一次提交的 thread.isLoading：effect 里更新，不在渲染期写 ref
  const threadRef = useRef(thread);
  useEffect(() => {
    threadRef.current = thread;
  });

  /** busy：上一次发送仍在途，或 SDK 的流（含停止后的收尾）还没结束。 */
  const isBusy = useCallback(
    () =>
      sendInFlightRef.current ||
      currentSubmissionRef.current?.state === "pending" ||
      threadRef.current?.isLoading === true,
    [],
  );

  // SDK 流收尾（isLoading 回落）时，受理后的 submission 若还没被 onFinish/onError
  // 收尾（如停止导致的 AbortError 不回调），按是否请求过停止来结案。
  useEffect(() => {
    if (thread.isLoading) return;
    // SDK 流收尾：手动重连的单飞记录随之清空（下一次重连是新的尝试）
    reconnectInFlightRef.current.clear();
    // 取消请求在途：completion 等取消结果（cancelled / cancelFailed）再结案
    if (cancelPendingRef.current) return;
    const current = currentSubmissionRef.current;
    if (current?.state === "accepted") {
      completeSubmission(current, {
        status: stopRequestedRef.current ? "stopped" : "finished",
      });
    } else if (current?.state === "pending" && current.submitted) {
      // 兜底：已入队的流收尾了，却既没有 onCreated 也没有 onError / onStop——
      // submission 永远停在 pending 会把 busy 锁占死，之后每次发送都被静默拒绝
      rejectSubmission(current, "failed", SEND_NOT_ACCEPTED_MESSAGE);
      toast.error(SEND_NOT_ACCEPTED_MESSAGE);
    }
    stopRequestedRef.current = false;
  }, [thread.isLoading, completeSubmission, rejectSubmission]);

  // Reset thread-local pending UI state when switching between threads so
  // optimistic messages and in-flight guards do not leak across chat views.
  useEffect(() => {
    startedRef.current = false;
    prevTailIdRef.current = undefined;
    const pending = currentSubmissionRef.current;
    if (pending?.state !== "pending") {
      sendInFlightRef.current = false;
      return;
    }
    // `undefined → 本次提交的预生成 UUID` 是身份认领（页面翻 isNewThread），不算切线程
    if (
      threadId !== null &&
      threadId !== undefined &&
      threadId === pending.requestedThreadId
    ) {
      return;
    }
    // 清理（optimistic / 上传态 / sendInFlight / 中止上传）都在 rejectSubmission 里
    sendInFlightRef.current = false;
    rejectSubmission(pending, "stopped", SEND_STOPPED_MESSAGE);
  }, [threadId, rejectSubmission]);

  // 卸载时取消在途上传（受理前阶段没有别的收尾时机）
  useEffect(
    () => () => {
      uploadOpRef.current?.controller.abort();
    },
    [],
  );

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

  /**
   * 提交一条消息。返回的 Promise 就是受理 Promise（PR2），所有调用方都必须观察它的
   * rejection：
   * - resolve：上传完成且 SDK `onCreated`（线程与 run 已创建）。
   * - reject `SendRejectedError`：`busy`（上一次发送或 SDK 的流尚未收尾；不提示，输入
   *   保留）、`stopped`（受理前停止 / 切线程；不提示）、`failed`（上传失败、创建前失败、
   *   流收尾却没有受理；已提示一次）。
   * 经 InputBox 的调用由 registry 的 PromptInput 兜住 rejection（Provider 模式下被拒
   * 不清空输入框）；直接调用的页面自己 catch。受理之后的完成状态只在 hook 内部管理。
   */
  const sendMessage = useCallback(
    async (
      threadId: string,
      message: PromptInputMessage,
      extraContext?: Record<string, unknown>,
      options?: SendMessageOptions,
    ) => {
      if (isBusy()) {
        // 不提示：输入保留，用户等上一条收尾后再发
        throw new SendRejectedError("busy", SEND_BUSY_MESSAGE);
      }
      const submission = createSubmission(
        ++submissionSeqRef.current,
        threadId,
        threadIdRef.current === null,
      );
      currentSubmissionRef.current = submission;
      sendInFlightRef.current = true;
      stopRequestedRef.current = false;
      const uploadOp = {
        id: ++uploadOpSeqRef.current,
        controller: new AbortController(),
      };
      uploadOpRef.current = uploadOp;
      // 归属检查：切线程 / 受理前停止后，本 submission 不再拥有任何后续步骤
      const stillOwner = () =>
        currentSubmissionRef.current === submission &&
        submission.state === "pending";

      const text = message.text?.trim() ?? "";

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

      let uploadedFileInfo: UploadedFileInfo[] = [];

      try {
        // Upload files first if any
        if (message.files && message.files.length > 0) {
          setIsUploading(true);
          setUploadProgress(0);
          try {
            const filePromises = message.files.map((fileUIPart) =>
              promptInputFilePartToFile(fileUIPart, {
                signal: uploadOp.controller.signal,
              }),
            );

            const conversionResults = await withAbort(
              uploadOp.controller.signal,
              Promise.all(filePromises),
            );
            // 附件 URL fallback 转换期间被取消：不进入上传
            if (!stillOwner() || uploadOp.controller.signal.aborted) {
              throw new SendRejectedError("stopped", SEND_STOPPED_MESSAGE);
            }
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
              const uploadResponse = await withAbort(
                uploadOp.controller.signal,
                uploadFilesWithProgress(
                  threadId,
                  files,
                  (loaded, total) => {
                    setUploadProgress(
                      total > 0 ? Math.round((loaded / total) * 100) : 0,
                    );
                  },
                  { signal: uploadOp.controller.signal },
                ),
              );
              // 上传期间切线程 / 停止：结果不属于当前操作
              if (!stillOwner() || uploadOp.controller.signal.aborted) {
                throw new SendRejectedError("stopped", SEND_STOPPED_MESSAGE);
              }
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
            if (!stillOwner() || uploadOp.controller.signal.aborted) {
              // 切线程 / 受理前停止：静默收尾，不提示
              rejectSubmission(submission, "stopped", SEND_STOPPED_MESSAGE);
            } else {
              const errorMessage =
                error instanceof Error
                  ? error.message
                  : "Failed to upload files.";
              // 停滞超时等已知形态映射成用户可读文案；其余沿用后端 detail / 网络错误原串
              toast.error(friendlyUploadErrorMessage(error) ?? errorMessage);
              rejectSubmission(submission, "failed", errorMessage, error);
            }
          } finally {
            setIsUploading(false);
            setUploadProgress(0);
          }
        }
        if (!stillOwner()) return submission.accepted;

        // Build files metadata for submission (included in additional_kwargs)
        const filesForSubmit: FileInMessage[] = uploadedFileInfo.map(
          (info) => ({
            filename: info.filename,
            size: info.size,
            path: info.virtual_path,
            status: "uploaded" as const,
          }),
        );

        const mode = normalizeMode(context.mode) ?? "standard";
        const selectedModel = models.find(
          (model) => model.name === context.model_name,
        );

        // onSend：附件转换与上传完成、提交 run 前——页面只用预生成 UUID 更新地址栏
        submission.onSendFired = true;
        listeners.current.onSend?.(threadId);
        if (!stillOwner()) return submission.accepted;

        submission.submitted = true;
        try {
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
                mode,
                thinking_enabled:
                  mode !== "flash" && selectedModel?.supports_thinking === true,
                is_plan_mode: mode !== "flash",
                subagent_enabled: mode === "ultra",
                reasoning_effort: mode === "flash" ? "minimal" : "high",
                thread_id: threadId,
              },
            },
          );
        } catch (error) {
          if (submission.state !== "pending") {
            // onCreated 先到、run 已受理：错误交给流回调处理，这里不再提示
            return submission.accepted;
          }
          // SDK 入队前就拒绝（同步校验）：受理前失败，提示一次
          const errorMessage = getStreamErrorMessage(error);
          toast.error(friendlySendErrorMessage(error) ?? errorMessage);
          rejectSubmission(submission, "failed", errorMessage, error);
          return submission.accepted;
        }
        void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
        // 受理与否由 SDK 回调决定：onCreated → resolve；创建前 onError / 停止 → reject。
        // 流收尾却没有回调的兜底在流收尾 effect 里（isLoading 回落时结案）。
        return submission.accepted;
      } finally {
        if (uploadOpRef.current?.id === uploadOp.id) uploadOpRef.current = null;
      }
    },
    [
      thread,
      t.uploads.uploadingFiles,
      context,
      models,
      queryClient,
      isBusy,
      rejectSubmission,
    ],
  );

  /**
   * 取消受理后的有界刷新（PR3）：失效 runs 列表，再重取该 run 的分片——服务端已把
   * run 置为 interrupted，子任务据此记为「已取消」而不是失败。只对仍停留在该线程的
   * 视图重取分片。
   */
  const syncRunAfterCancel = useCallback(
    async (threadId: string, runId: string) => {
      await queryClient.invalidateQueries({ queryKey: ["thread", threadId] });
      if (threadIdRef.current === threadId) await refetchRunHistory(runId);
    },
    [queryClient, refetchRunHistory],
  );

  /** 一次显式取消的结果处理：失败（含等待超时）→ 提示并保留可重试目标；受理 → 有界刷新。 */
  const applyCancelOutcome = useCallback(
    async (threadId: string, runId: string) => {
      let outcome: CancelOutcome;
      try {
        // wait=true 要等服务端终态；卡死 / 半开时不能让 busy 锁永远不放
        outcome = await withTimeout(
          cancelRun(threadId, runId),
          CANCEL_WAIT_LIMIT_MS,
          () =>
            new Error(
              `停止超时：服务端 ${CANCEL_WAIT_LIMIT_MS / 1000} 秒内没有确认停止`,
            ),
        );
      } catch (error) {
        outcome = { status: "failed", error };
      }
      if (outcome.status === "failed") {
        toast.error(`停止失败：${getStreamErrorMessage(outcome.error)}`);
        setCancelFailure({ threadId, runId });
      } else {
        setCancelFailure(null);
        if (outcome.status === "cancelled") {
          await syncRunAfterCancel(threadId, runId).catch((error: unknown) => {
            console.error("Failed to refresh run after cancel", error);
          });
        }
      }
      return outcome;
    },
    [syncRunAfterCancel],
  );

  /** 已受理的 run：等可等待的 cancel，结果写入 submission 的 completion。 */
  const cancelAcceptedRun = useCallback(
    async (submission: Submission) => {
      if (!submission.runId) return;
      cancelPendingRef.current = true;
      try {
        const outcome = await applyCancelOutcome(
          submission.threadId ?? submission.requestedThreadId,
          submission.runId,
        );
        if (outcome.status === "failed") {
          completeSubmission(submission, {
            status: "cancelFailed",
            error: outcome.error,
          });
        } else {
          completeSubmission(submission, { status: "stopped" });
        }
      } finally {
        cancelPendingRef.current = false;
      }
    },
    [applyCancelOutcome, completeSubmission],
  );

  /** 恢复入口的「重试停止」：再次调用同一 cancel（PR3）。 */
  const retryCancel = useCallback(async () => {
    if (!cancelFailure) return;
    await applyCancelOutcome(cancelFailure.threadId, cancelFailure.runId);
  }, [applyCancelOutcome, cancelFailure]);

  /**
   * 停止当前 run（PR2）：受理前的 submission 由这里直接以 stopped reject（中止上传，
   * 不依赖 SDK 的 onStop 是否触发；此时服务端尚无 runId，不发 cancel）；再中断 SDK 的
   * 本地流；已受理的再 await 可等待的 cancel——受理 → stopped，失败 → cancelFailed
   * 并提示一次（不伪造 interrupted，重试入口由 PR3 提供）。
   */
  const stop = useCallback(async () => {
    stopRequestedRef.current = true;
    const pending = currentSubmissionRef.current;
    if (pending?.state === "pending") {
      rejectSubmission(pending, "stopped", SEND_STOPPED_MESSAGE);
    }
    // 已受理：先占住「取消在途」，让流收尾 effect 确定性地等取消结果（否则 isLoading
    // 先回落会把 completion 记成 stopped，随后 cancel 失败就无处落脚）
    if (currentSubmissionRef.current?.state === "accepted") {
      cancelPendingRef.current = true;
    }
    try {
      await thread.stop();
    } catch (error) {
      // SDK 的 stop 不该抛；真抛了也不能让停止按钮产生未处理拒绝
      toast.error(`停止失败：${getStreamErrorMessage(error)}`);
    } finally {
      const submission = currentSubmissionRef.current;
      if (submission?.state === "accepted" && submission.runId) {
        await cancelAcceptedRun(submission);
      } else {
        cancelPendingRef.current = false;
      }
    }
  }, [thread, rejectSubmission, cancelAcceptedRun]);

  // ── PR3：断线恢复 owner ───────────────────────────────────────────────
  const activeRunStatus = activeRunId
    ? runsById[activeRunId]?.status
    : undefined;
  // 流断了、run 可能还在服务端跑：才给重连入口
  const recoverableDisconnect = isRecoverableDisconnect({
    error: thread.error,
    isLoading: thread.isLoading,
    hasRun: activeRunId !== null,
    runStatus: activeRunStatus,
  });
  /** 取消失败只对「仍是当前 run」有效：换了 run / 换了线程就不再是它的待办。 */
  const cancelFailed =
    cancelFailure !== null &&
    cancelFailure.threadId === onStreamThreadId &&
    cancelFailure.runId === activeRunId;

  /**
   * 用户在连接类错误后手动重连（PR3）：按 (threadId, runId) 单飞；SDK 流在途（含
   * mount 自动 rejoin）时不重复 join——自动恢复归 SDK。join 的 Promise 只表示排队，
   * 连接结果由 onFinish / onError 驱动，这里不判定成功。stale run 404 由 onError
   * 清 key 并失效 runs / history；业务 4xx 不进这里。
   */
  const reconnectActiveRun = useCallback(async () => {
    const runId = activeRunId;
    const currentThreadId = threadIdRef.current;
    if (!runId || !currentThreadId) return;
    if (thread.isLoading) return;
    const key = `${currentThreadId}:${runId}`;
    if (reconnectInFlightRef.current.has(key)) return;
    reconnectInFlightRef.current.add(key);
    setIsReconnecting(true);
    try {
      await thread.joinStream(runId);
    } catch (error) {
      if (classifyStreamError(error) !== "aborted") {
        toast.error(getStreamErrorMessage(error));
      }
    } finally {
      setIsReconnecting(false);
    }
  }, [activeRunId, thread]);

  // 流出错收尾后，run 在服务端已走到终态（runs 列表刷新得知）：没有 onFinish 替它
  // 重取分片，这里按新鲜度补取一次，断线期间产生的尾段消息才不会丢
  useEffect(() => {
    if (!activeRunId || thread.isLoading || !thread.error) return;
    const run = runsById[activeRunId];
    if (!run || !isTerminalRunStatus(run.status)) return;
    void refetchRunIfStaleHistory(run.run_id).catch((error: unknown) => {
      console.error("Failed to refetch settled run history", error);
    });
  }, [
    activeRunId,
    thread.isLoading,
    thread.error,
    runsById,
    refetchRunIfStaleHistory,
  ]);

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
    stop,
    isUploading,
    uploadProgress,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
    messageRunIds,
    runsById,
    /** 当前流所属的 run（SDK onCreated 回传）；没有在途 / 历史流时为 null。 */
    activeRunId,
    /** 流因连接类错误收尾、run 可能仍在服务端运行：给重连入口。 */
    isRecoverableDisconnect: recoverableDisconnect,
    reconnectActiveRun,
    isReconnecting,
    /** 最近一次显式取消失败（含超时），仍是当前 run 时为 true：恢复入口提供重试。 */
    cancelFailed,
    retryCancel,
  } as const;
}

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

const HISTORY_PAGE_SIZE = 200;

/**
 * 分页拉全单 run 的历史消息（后端 GET /api/threads/{tid}/runs/{rid}/messages：
 * 首页不带游标、返回**最新**的 limit 条、页内 seq 升序；`has_more` 时用本页第一条的
 * seq 作 `before_seq` 继续向前取旧页）。PR3（2026-11-24 前端可靠性批）修正了原先
 * 「首页是最早页 + after_seq」的错误假设：真实 API 的 after_seq 只会反复取到最新页。
 * 不再保留 25 页 / 5000 条的静默截断——长 run 的首条提示词不能丢；防死循环只靠
 * 「游标必须严格向前（变小）」，不前进或空页直接抛错。middleware caller 消息在此过滤。
 */
export async function fetchAllRunMessages(
  threadId: string,
  runId: string,
  fetchImpl: FetchLike = fetch,
): Promise<RunMessage[]> {
  const collected = new Map<number, RunMessage>();
  let beforeSeq: number | undefined;
  for (;;) {
    const params = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE) });
    if (beforeSeq !== undefined) {
      params.set("before_seq", String(beforeSeq));
    }
    const result: { data?: RunMessage[]; has_more?: boolean } = await fetchImpl(
      `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/messages?${params}`,
      {
        method: "GET",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
      },
    ).then((res) => res.json());
    const rows = Array.isArray(result.data) ? result.data : [];
    for (const row of rows) {
      collected.set(row.seq, row);
    }
    if (!result.has_more) break;
    const firstSeq = rows[0]?.seq;
    // 游标必须严格前进：下一页的 before_seq 是本页第一条的 seq，它必须小于上一轮的游标
    if (
      typeof firstSeq !== "number" ||
      (beforeSeq !== undefined && firstSeq >= beforeSeq)
    ) {
      throw new Error(
        `Run messages pagination cursor did not advance (before_seq=${beforeSeq}, first seq=${firstSeq})`,
      );
    }
    beforeSeq = firstSeq;
  }
  return [...collected.values()]
    .sort((a, b) => a.seq - b.seq)
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

/** 一个 run 的历史分片：消息 + 加载时该 run 的 status / updated_at（新鲜度判定用）。 */
export interface HistoryShard {
  runId: string;
  messages: Message[];
  status: string | undefined;
  updatedAt: string | undefined;
  /** 加载后是否仍新鲜：重取失败保留旧分片时置 false。 */
  fresh: boolean;
}

type HistoryState = {
  threadId: string;
  shards: Map<string, HistoryShard>;
  loadedRunIds: Set<string>;
  loading: boolean;
};

const EMPTY_HISTORY_STATE: HistoryState = {
  threadId: "",
  shards: new Map(),
  loadedRunIds: new Set(),
  loading: false,
};

/**
 * run 历史（PR3 重写）：runs 列表是 newest-first，首次只加载最新的 run，loadMore
 * 向前一个 run；每个 run 一个分片，按旧→新、run 内按 seq 拼接。
 *
 * - generation：切线程后旧线程的迟到响应一律丢弃（messages 不串台）
 * - requestEpochByRun：同一 run 的多次请求只让最新一次原子替换分片（慢旧请求不回退）
 * - loadedRunIds 只在完整成功后写入；失败保留旧分片、不标记新鲜，可重试
 * - onFinish 用 meta.run_id 指向 `refetchRun` 强制重取（mount 自动 rejoin 不经过 onCreated）
 */
export function useThreadHistory(threadId: string) {
  const runs = useThreadRuns(threadId);
  const queryClient = useQueryClient();
  const [state, setState] = useState<HistoryState>(EMPTY_HISTORY_STATE);
  const generationRef = useRef(0);
  const requestEpochRef = useRef(new Map<string, number>());
  const inFlightRef = useRef(new Map<string, Promise<void>>());
  const runsRef = useRef(runs.data);
  const stateRef = useRef(state);

  useEffect(() => {
    runsRef.current = runs.data;
  }, [runs.data]);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  /** 写入当前线程的历史状态；上一线程的状态残留时从空状态起步（= 切线程整体重置）。 */
  const updateState = useCallback(
    (update: (prev: HistoryState) => HistoryState) => {
      setState((prev) =>
        update(
          prev.threadId === threadId
            ? prev
            : {
                threadId,
                shards: new Map(),
                loadedRunIds: new Set(),
                loading: false,
              },
        ),
      );
    },
    [threadId],
  );

  /** 加载 / 重取一个 run 的全部分片；只让该 run 最新一次请求写入。 */
  const loadRun = useCallback(
    async (run: Run): Promise<void> => {
      const runId = run.run_id;
      const epoch = (requestEpochRef.current.get(runId) ?? 0) + 1;
      requestEpochRef.current.set(runId, epoch);
      const generation = generationRef.current;
      const token: { promise: Promise<void> | null } = { promise: null };
      const request = (async () => {
        updateState((prev) => ({ ...prev, loading: true }));
        try {
          const all = await fetchAllRunMessages(threadId, runId);
          if (
            generationRef.current !== generation ||
            requestEpochRef.current.get(runId) !== epoch
          ) {
            return;
          }
          const shard: HistoryShard = {
            runId,
            messages: all.map((m) => m.content),
            status: run.status,
            updatedAt: run.updated_at,
            fresh: true,
          };
          updateState((prev) => ({
            ...prev,
            shards: new Map(prev.shards).set(runId, shard),
            loadedRunIds: new Set(prev.loadedRunIds).add(runId),
          }));
        } catch (error) {
          if (generationRef.current !== generation) return;
          // 失败保留旧分片，但不再把它当作新鲜
          updateState((prev) => {
            const shards = new Map(prev.shards);
            const old = shards.get(runId);
            if (old) shards.set(runId, { ...old, fresh: false });
            return { ...prev, shards };
          });
          throw error;
        } finally {
          if (generationRef.current === generation) {
            // 只在仍是本次请求占位时释放（可能已被更新的请求替换）
            if (inFlightRef.current.get(runId) === token.promise) {
              inFlightRef.current.delete(runId);
            }
            updateState((prev) =>
              inFlightRef.current.size === 0
                ? { ...prev, loading: false }
                : prev,
            );
          }
        }
      })();
      token.promise = request;
      inFlightRef.current.set(runId, request);
      return request;
    },
    [threadId, updateState],
  );

  // 切线程：换代，旧线程的在途响应因 generation 不再写入；状态本身由 updateState 按线程重置
  useEffect(() => {
    generationRef.current += 1;
    requestEpochRef.current.clear();
    inFlightRef.current.clear();
  }, [threadId]);

  /** 当前线程的历史快照（上一线程的残留视为空）。 */
  const snapshot = useCallback(
    () =>
      stateRef.current.threadId === threadId
        ? stateRef.current
        : EMPTY_HISTORY_STATE,
    [threadId],
  );

  // runs 到位后加载最新的 run（newest-first 的第 0 个）
  useEffect(() => {
    const latest = runsRef.current?.[0];
    if (!latest) return;
    if (inFlightRef.current.has(latest.run_id)) return;
    if (snapshot().loadedRunIds.has(latest.run_id)) return;
    void loadRun(latest).catch((error: unknown) => {
      console.error("Failed to load thread history", error);
    });
  }, [runs.data, loadRun, snapshot]);

  /** 只前进一个 run：旧→新方向上第一个尚未加载的 run。 */
  const loadMore = useCallback(async () => {
    const list = runsRef.current ?? [];
    for (const run of [...list].reverse()) {
      if (snapshot().loadedRunIds.has(run.run_id)) continue;
      if (inFlightRef.current.has(run.run_id)) {
        return inFlightRef.current.get(run.run_id);
      }
      return loadRun(run);
    }
  }, [loadRun, snapshot]);

  /** 强制重取指定 run（onFinish 用 meta.run_id 定位）；runs 列表里没有时先刷新列表。 */
  const refetchRun = useCallback(
    async (runId: string): Promise<void> => {
      let run = runsRef.current?.find((r) => r.run_id === runId);
      if (!run) {
        await queryClient.invalidateQueries({ queryKey: ["thread", threadId] });
        run = queryClient
          .getQueryData<Run[]>(["thread", threadId])
          ?.find((r) => r.run_id === runId);
      }
      if (!run) return;
      await loadRun(run);
    },
    [queryClient, threadId, loadRun],
  );

  /**
   * 分片相对 runs 列表已过期时才重取（新鲜度判定）：加载后 run 的 status / updated_at
   * 变了，或上次加载失败；终态且未变的 run 跳过。
   */
  const refetchRunIfStale = useCallback(
    async (runId: string): Promise<void> => {
      const run = runsRef.current?.find((r) => r.run_id === runId);
      if (!run) return;
      // 该 run 已有在途的加载：它写完的分片就带着当前状态，等它即可（不叠一次请求）
      const inFlight = inFlightRef.current.get(runId);
      if (inFlight) {
        await inFlight;
        return;
      }
      const shard = snapshot().shards.get(runId);
      if (
        shard &&
        shard.fresh &&
        shard.status === run.status &&
        shard.updatedAt === run.updated_at
      ) {
        return;
      }
      await loadRun(run);
    },
    [loadRun, snapshot],
  );

  // 不在 runs 列表里的分片（如被 regenerate 隐藏）在拼接时自然被过滤，不需要额外清理
  const current = state.threadId === threadId ? state : EMPTY_HISTORY_STATE;
  const orderedRuns = useMemo(() => {
    const list = runs.data ?? [];
    return [...list]
      .reverse()
      .filter((r) => current.shards.has(r.run_id))
      .map((r) => ({ run: r, shard: current.shards.get(r.run_id)! }));
  }, [runs.data, current.shards]);
  const { messages, messageRunIds } = useMemo(() => {
    const out: Message[] = [];
    const runIds = new Map<string, string>();
    for (const { run, shard } of orderedRuns) {
      const seen = new Set<string>();
      shard.messages.forEach((message, index) => {
        const key = message.id ?? `seq:${index}`;
        if (seen.has(key)) return;
        seen.add(key);
        out.push(message);
        if (message.id) runIds.set(message.id, run.run_id);
      });
    }
    return { messages: out, messageRunIds: runIds };
  }, [orderedRuns]);
  const runsById = useMemo(() => {
    const out: Record<string, Run> = {};
    for (const run of runs.data ?? []) out[run.run_id] = run;
    return out;
  }, [runs.data]);

  const hasMore = orderedRuns.length < (runs.data?.length ?? 0);
  return {
    runs: runs.data,
    runsById,
    messages,
    messageRunIds,
    loading: current.loading,
    hasMore,
    loadMore,
    refetchRun,
    refetchRunIfStale,
    loadedRunIds: current.loadedRunIds,
  } as const;
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
      // 后端路由当前忽略 limit、固定上限 100；显式传值是防御——路由日后支持 limit 时
      // 不致回退到 SDK 默认的 10 条。超过 100 个 run 的恢复是明确的未覆盖边界。
      const response = await apiClient.runs.list(threadId, { limit: 100 });
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
