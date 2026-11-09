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

function createCompatibleClient(): LangGraphClient {
  const apiUrl = getLangGraphBaseURL();
  const client = new LangGraphClient({
    apiUrl,
    onRequest: injectCsrfHeader,
  });

  // Creating a run is not idempotent. Retrying an ambiguous gateway failure
  // can create the same run more than once after the backend accepted the
  // original request. The SDK also uses this client's transport for recovery
  // GETs, which must retain normal HTTP retries (including transient 5xx).
  const streamRecoveryClient = new StreamRecoveryClient({ apiUrl });
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
  const originalCancel = client.runs.cancel.bind(client.runs);
  client.runs.cancel = (async (threadId, runId, wait, action) => {
    try {
      return await originalCancel(threadId, runId, wait, action);
    } catch (error) {
      if (isRunNotCancellableError(error)) {
        clearReconnectRun(threadId, runId);
        return;
      }
      throw error;
    }
  }) as typeof client.runs.cancel;

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
