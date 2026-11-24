"use client";

import {
  Client as LangGraphClient,
  RunsClient,
} from "@langchain/langgraph-sdk/client";

import { getLangGraphBaseURL } from "../config";
import { STREAM_REJOIN_KEY_PREFIX } from "../gallery/stream-rejoin";

import { isStateChangingMethod, readCsrfCookie } from "./fetcher";
import { sanitizeRunStreamOptions } from "./stream-mode";

/**
 * SDK ``onRequest`` hook that mints the ``X-CSRF-Token`` header from the
 * live ``csrf_token`` cookie just before each outbound fetch.
 *
 * Reading the cookie per-request (rather than baking it into the SDK's
 * ``defaultHeaders`` at construction) handles login / logout / password
 * change cookie rotation transparently. Both the ``/api/langgraph/*`` SDK
 * path and the direct REST endpoints in ``fetcher.ts:fetchWithAuth``
 * share :func:`readCsrfCookie` and :const:`STATE_CHANGING_METHODS` so
 * the contract stays in lockstep.
 */
function injectCsrfHeader(_url: URL, init: RequestInit): RequestInit {
  if (!isStateChangingMethod(init.method ?? "GET")) {
    return init;
  }
  const token = readCsrfCookie();
  if (!token) return init;
  const headers = new Headers(init.headers);
  if (!headers.has("X-CSRF-Token")) {
    headers.set("X-CSRF-Token", token);
  }
  return { ...init, headers };
}

/**
 * Matches the gateway's terminal-state cancel conflict, raised by
 * ``cancel_run`` in ``backend/app/gateway/routers/thread_runs.py`` as
 * ``Run X is not cancellable (status: success|error)``.
 *
 * Repeated cancels of an already-interrupted run are idempotent server-side,
 * so what reaches here is the finish-window race: the backend flipped the run
 * to ``success`` but the SSE stream had not drained yet, and the user pressed
 * stop. The run is already stopped, so the caller's intent holds.
 *
 * Deliberately narrow. A 409 whose message says the run is still active
 * elsewhere is a real failure to cancel a live run and must stay visible.
 */
function isRunNotCancellableError(error: unknown): boolean {
  const status =
    typeof error === "object" && error !== null
      ? Reflect.get(error, "status")
      : undefined;
  const message =
    typeof error === "string"
      ? error
      : error instanceof Error
        ? error.message
        : typeof error === "object" && error !== null
          ? String(Reflect.get(error, "message") ?? "")
          : "";

  return (
    (status === 409 || message.includes("HTTP 409")) &&
    message.includes("is not cancellable")
  );
}

/**
 * Drop the SDK's reconnect key for a run that can no longer be rejoined.
 *
 * Only removes the key when it still points at this run, so a newer run's
 * key — written between the cancel request and its rejection — is preserved.
 */
function clearReconnectRun(
  threadId: string | null | undefined,
  runId: string,
): void {
  if (typeof window === "undefined" || !threadId) return;

  const key = `${STREAM_REJOIN_KEY_PREFIX}${threadId}`;
  try {
    const storage = window.sessionStorage;
    if (storage.getItem(key) === runId) {
      storage.removeItem(key);
    }
  } catch {
    // Ignore storage failures so cancel cleanup never throws.
  }
}

// Reuse the SDK's retry budget, backoff and HTTP error handling for recovery
// requests that have already been prepared by the run-creation client.
class StreamRecoveryClient extends RunsClient {
  fetchWithRetries(...args: Parameters<typeof fetch>): Promise<Response> {
    return this.asyncCaller.fetch(...args);
  }
}

/**
 * SDK 自带的 AsyncCaller 只按 message 前缀识别中止（"AbortError" / "Cancel"，见
 * `@langchain/langgraph-sdk` 1.6.0 的 utils/async_caller），而 Chromium 对
 * `AbortController.abort()` 的拒绝消息是 "signal is aborted without reason"（name
 * 才是 AbortError）——SDK 会把已中止的请求当成普通失败进入退避重试（默认 4 次、
 * 约 15 秒），受理前停止后流迟迟不收尾。这里把中止 rejection 归一成 SDK 认得的
 * 形态，让重试立即结束；原始异常通过 cause 保留，其余错误原样透传。
 */
async function fetchNormalizingAbort(
  ...args: Parameters<typeof fetch>
): Promise<Response> {
  try {
    return await fetch(...args);
  } catch (error) {
    if (
      typeof error === "object" &&
      error !== null &&
      Reflect.get(error, "name") === "AbortError"
    ) {
      // 现代浏览器支持 Error cause；旧环境忽略第二个参数
      const normalized = new Error(
        `AbortError: ${String(Reflect.get(error, "message") ?? "")}`,
        { cause: error },
      );
      normalized.name = "AbortError";
      throw normalized;
    }
    throw error;
  }
}

// ── PR2（2026-11-24 前端可靠性批）：可等待的取消 ────────────────────────────

/** 取消请求的结果：永不 reject，由调用方决定 completion 与提示。 */
export type CancelOutcome =
  | { status: "cancelled" }
  | { status: "not_cancellable" }
  | { status: "failed"; error: unknown };

/** 与 SDK `RunsClient.cancel` 同形的签名（wait / action 可缺省）。 */
type CancelAction = "interrupt" | "rollback";
type RawCancel = (
  threadId: string,
  runId: string,
  wait?: boolean,
  action?: CancelAction,
  options?: { signal?: AbortSignal },
) => Promise<void>;

/** SDK 原始 cancel：createCompatibleClient 绑定后登记，cancelRun 直接用它（只走一层去重）。 */
let rawCancel: RawCancel | null = null;

/**
 * 显式取消固定 wait=true：等服务端把 run 置为终态再返回，hook 据此把 completion
 * 记为 stopped；wait=false 只是受理确认，run 可能仍在跑。action 为 interrupt（用户停止）。
 */
const CANCEL_WAIT = true;
const CANCEL_ACTION: CancelAction = "interrupt";

/** 同一 (thread, run, wait, action) 的并发取消合并为一次请求（服务端对重复取消幂等）。 */
const inFlightCancels = new Map<string, Promise<CancelOutcome>>();

const CANCEL_STATUSES = new Set<string>([
  "cancelled",
  "not_cancellable",
  "failed",
]);

function isCancelOutcome(value: unknown): value is CancelOutcome {
  if (typeof value !== "object" || value === null) return false;
  const status = Reflect.get(value, "status");
  return typeof status === "string" && CANCEL_STATUSES.has(status);
}

/**
 * 把一次 cancel 请求映射为 CancelOutcome：成功 → cancelled；终态 409 →
 * not_cancellable（清 reconnect key、视为已停止）；其余失败 → failed（保留错误）。
 * 已经是 outcome 的结果（命中同一在途请求时）原样透传。
 */
async function cancelWithOutcome(
  threadId: string,
  runId: string,
  request: Promise<unknown>,
): Promise<CancelOutcome> {
  try {
    const result = await request;
    return isCancelOutcome(result) ? result : { status: "cancelled" };
  } catch (error) {
    if (isRunNotCancellableError(error)) {
      clearReconnectRun(threadId, runId);
      return { status: "not_cancellable" };
    }
    return { status: "failed", error };
  }
}

function dedupeCancel(
  threadId: string,
  runId: string,
  wait: boolean,
  action: string,
  start: () => Promise<unknown>,
): Promise<CancelOutcome> {
  const key = `${threadId}:${runId}:${wait ? "1" : "0"}:${action}`;
  const existing = inFlightCancels.get(key);
  if (existing) return existing;
  const request = cancelWithOutcome(threadId, runId, start()).finally(() => {
    inFlightCancels.delete(key);
  });
  inFlightCancels.set(key, request);
  return request;
}

/**
 * 可等待的取消（hook 的停止路径用）：`thread.stop()` 里 SDK 发出的 cancel 是浮动
 * Promise、接不到结果；这里显式等同一请求的结果。永不 reject。
 */
export function cancelRun(
  threadId: string,
  runId: string,
): Promise<CancelOutcome> {
  getAPIClient();
  return dedupeCancel(threadId, runId, CANCEL_WAIT, CANCEL_ACTION, () =>
    rawCancel!(threadId, runId, CANCEL_WAIT, CANCEL_ACTION),
  );
}

function createCompatibleClient(): LangGraphClient {
  const apiUrl = getLangGraphBaseURL();
  const client = new LangGraphClient({
    apiUrl,
    onRequest: injectCsrfHeader,
    callerOptions: { fetch: fetchNormalizingAbort },
  });

  // Creating a run is not idempotent. Retrying an ambiguous gateway failure
  // can create the same run more than once after the backend accepted the
  // original request. The SDK also uses this client's transport for recovery
  // GETs, which must retain normal HTTP retries (including transient 5xx).
  const streamRecoveryClient = new StreamRecoveryClient({
    apiUrl,
    callerOptions: { fetch: fetchNormalizingAbort },
  });
  const runCreationClient = new RunsClient({
    apiUrl,
    callerOptions: {
      maxRetries: 0,
      fetch: (...args: Parameters<typeof fetch>) =>
        args[1]?.method === "GET"
          ? streamRecoveryClient.fetchWithRetries(...args)
          : fetch(...args),
    },
    onRequest: injectCsrfHeader,
  });
  const originalRunStream = runCreationClient.stream.bind(runCreationClient);
  client.runs.stream = ((threadId, assistantId, payload) =>
    originalRunStream(
      threadId,
      assistantId,
      sanitizeRunStreamOptions(payload),
    )) as typeof client.runs.stream;

  const originalJoinStream = client.runs.joinStream.bind(client.runs);
  client.runs.joinStream = ((threadId, runId, options) =>
    originalJoinStream(
      threadId,
      runId,
      sanitizeRunStreamOptions(options),
    )) as typeof client.runs.joinStream;

  // The stop button's cancel is issued from inside the SDK's own stream
  // callback as a floating promise, so a rejection there cannot be caught by
  // `await thread.stop()` in component code — it lands as an unhandled
  // rejection. Containing it has to happen here, at the client boundary.
  // 运行时返回 CancelOutcome（SDK 类型仍声明 Promise<void>，这处断言无法避免）；
  // 需要结果的调用方一律走 cancelRun。
  rawCancel = client.runs.cancel.bind(client.runs);
  client.runs.cancel = ((
    threadId: string,
    runId: string,
    wait = false,
    action: CancelAction = "interrupt",
  ) =>
    dedupeCancel(threadId, runId, wait, action, () =>
      rawCancel!(threadId, runId, wait, action),
    )) as unknown as typeof client.runs.cancel;

  return client;
}

const _clients = new Map<string, LangGraphClient>();
export function getAPIClient(): LangGraphClient {
  const cacheKey = "default";
  let client = _clients.get(cacheKey);

  if (!client) {
    client = createCompatibleClient();
    _clients.set(cacheKey, client);
  }

  return client;
}
