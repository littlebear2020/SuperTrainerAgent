/**
 * useThreadHistory 的测试夹具（thread-history-idempotence / -reset 共用）。
 *
 * 必须是测试文件的**第一个 import**：这里用 vi.mock 注册的 `@/core/api` 与
 * `@/core/api/fetcher` 替身要在 `@/core/threads/hooks` 求值之前生效。
 *
 * - runs 列表按真实 API 形态由夹具直接给出（newest-first）
 * - run messages 端点由 `respond(runId)` 决定：返回页面数组，或抛错；也可返回
 *   一个 deferred 让用例控制慢响应
 */

import type { Run } from "@langchain/langgraph-sdk";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement, type ReactElement, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { vi } from "vitest";

import { useThreadHistory } from "@/core/threads/hooks";

export type RunPage = { data: unknown[]; has_more: boolean };

export type MessageResponder = (
  runId: string,
  query: URLSearchParams,
) => RunPage[] | Promise<RunPage[]>;

const history = vi.hoisted(() => ({
  runsByThread: new Map<string, Run[]>(),
  listCalls: [] as Array<[string, unknown]>,
  messageCalls: [] as Array<{ threadId: string; runId: string; query: string }>,
  responder: null as MessageResponder | null,
}));

vi.mock("@/core/api", () => ({
  getAPIClient: () => ({
    runs: {
      list: (threadId: string, options: unknown) => {
        history.listCalls.push([threadId, options]);
        return Promise.resolve(history.runsByThread.get(threadId) ?? []);
      },
    },
  }),
}));
vi.mock("@/core/api/fetcher", () => ({
  fetch: (input: string) => {
    const url = new URL(String(input), "http://localhost");
    const match = /\/threads\/([^/]+)\/runs\/([^/]+)\/messages/.exec(
      url.pathname,
    );
    const threadId = match?.[1] ?? "";
    const runId = match?.[2] ?? "";
    history.messageCalls.push({
      threadId,
      runId,
      query: url.search.replace(/^\?/, ""),
    });
    const responder = history.responder;
    if (!responder)
      return Promise.reject(new Error("history responder not installed"));
    // responder 抛错 / 返回被拒绝的 Promise = 网络失败（fetch reject）
    return Promise.resolve()
      .then(() => responder(runId, url.searchParams))
      .then((pages) => ({
        ok: true,
        status: 200,
        json: () => {
          const next = pages.shift();
          if (!next) throw new Error("no page left");
          return Promise.resolve(next);
        },
      }));
  },
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
  getLangGraphBaseURL: () => "",
}));
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

export type HistoryApi = ReturnType<typeof useThreadHistory>;

const captured: { api: HistoryApi | null } = { api: null };

function Probe({ threadId }: { threadId: string }) {
  const api = useThreadHistory(threadId);
  // 渲染期不改外部变量：提交后由 effect 发布给用例
  useEffect(() => {
    captured.api = api;
  });
  return null;
}

export function run(
  runId: string,
  status = "success",
  createdAt = "2025-01-01T00:00:00Z",
): Run {
  return {
    run_id: runId,
    thread_id: "t",
    assistant_id: "lead_agent",
    status,
    created_at: createdAt,
    updated_at: createdAt,
    metadata: {},
    multitask_strategy: "reject",
    kwargs: {},
  } as unknown as Run;
}

export function runPage(
  runId: string,
  ids: string[],
  hasMore = false,
  startSeq = 1,
): RunPage {
  return {
    data: ids.map((id, i) => ({
      run_id: runId,
      seq: startSeq + i,
      content: { id, type: "human", content: id },
      metadata: { caller: "user" },
      created_at: "2025-01-01T00:00:00Z",
    })),
    has_more: hasMore,
  };
}

/** 夹具状态（runs 列表、请求记录、responder）。以函数暴露：vi.hoisted 变量不能直接导出。 */
export function harness() {
  return history;
}

export function resetHistoryHarness() {
  history.runsByThread.clear();
  history.listCalls.length = 0;
  history.messageCalls.length = 0;
  history.responder = null;
  captured.api = null;
}

/** 在 act 内等待一次 hook 操作并把结果渲染提交，再读快照。 */
export async function settleHistory<T>(
  operation: () => Promise<T>,
): Promise<T> {
  let result!: T;
  await act(async () => {
    result = await operation();
  });
  await flush();
  return result;
}

/** 同 settleHistory，但吞掉 rejection 并返回错误（失败用例用）。 */
export async function rejectsWithHistory<T>(
  operation: () => Promise<T>,
): Promise<unknown> {
  try {
    await settleHistory(operation);
  } catch (error) {
    return error;
  }
  return undefined;
}

export async function flush(ms = 20) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;
let queryClient: QueryClient | null = null;

export async function mountHistory(threadId: string): Promise<HistoryApi> {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await renderHistory(threadId);
  await flush();
  return historyApi();
}

export async function renderHistory(threadId: string) {
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client: queryClient! },
        createElement(Probe, { threadId }),
      ) as ReactElement,
    );
  });
  await flush();
}

export function historyApi(): HistoryApi {
  if (!captured.api) throw new Error("useThreadHistory not mounted");
  return captured.api;
}

export async function unmountHistory() {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  root = null;
  container = null;
  queryClient = null;
}

export function deferred<T>() {
  const holder: {
    resolve: (value: T) => void;
    reject: (error: unknown) => void;
    promise: Promise<T>;
  } = {
    resolve: () => undefined,
    reject: () => undefined,
    promise: null as unknown as Promise<T>,
  };
  holder.promise = new Promise<T>((resolve, reject) => {
    holder.resolve = resolve;
    holder.reject = reject;
  });
  return holder;
}

export function messageIds(api: HistoryApi): string[] {
  return api.messages.map((m) => m.id ?? "");
}
