/**
 * ``runs.cancel`` error handling in the API client.
 *
 * Clicking stop while a run is in its finish window (backend already flipped
 * to ``success``, SSE hasn't drained) makes the gateway answer 409
 * "is not cancellable". The SDK rejects, and because the call happens inside a
 * floating promise in the SDK's own stream callback, `await thread.stop()`
 * in component code cannot catch it.
 *
 * PR2（2026-11-24 前端可靠性批）：cancel 包装永不 reject，改为返回 `CancelOutcome`：
 * 成功 → `cancelled`；终态 409 → `not_cancellable`（清 reconnect key、视为已停止）；
 * 其余失败 → `failed`（保留错误，由调用方决定提示与重试）。SDK 内部的浮动调用与
 * hook 显式 await 的 `cancelRun` 走同一个包装，同一 runId 的并发取消合并为一次请求。
 */

import { useStream } from "@langchain/langgraph-sdk/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const cancelMock = vi.fn();
const streamMock = vi.fn();
const joinStreamMock = vi.fn();
const clientConfigs: Array<{
  callerOptions?: { fetch?: typeof fetch };
}> = [];

vi.mock("@langchain/langgraph-sdk/client", () => ({
  Client: class {
    // 真实 useStream 挂载时会读线程状态；这里给最小替身
    threads = {
      getHistory: async () => [],
      getState: async () => ({ checkpoint: null, values: {} }),
      updateState: async () => ({}),
      update: async () => ({}),
    };
    constructor(config: { callerOptions?: { fetch?: typeof fetch } }) {
      clientConfigs.push(config);
    }
    runs = {
      cancel: cancelMock,
      stream: streamMock,
      joinStream: joinStreamMock,
    };
  },
  // Run creation is bound off a standalone RunsClient (see api-client.ts);
  // the mock only needs a bindable `stream` for client construction.
  RunsClient: class {
    stream = streamMock;
  },
}));

vi.mock("../../../../src/core/config", () => ({
  getLangGraphBaseURL: () => "http://localhost:2024",
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

/** Build an SDK-shaped HTTP error. */
function httpError(
  status: number,
  message: string,
): Error & { status: number } {
  const error = new Error(message) as Error & { status: number };
  error.status = status;
  return error;
}

const NOT_CANCELLABLE = () =>
  httpError(409, "HTTP 409: Run run-1 is not cancellable (status: success)");

async function freshClient() {
  vi.resetModules();
  const { getAPIClient } = await import("@/core/api/api-client");
  return getAPIClient();
}

async function freshCancelRun() {
  vi.resetModules();
  const { cancelRun } = await import("@/core/api/api-client");
  return cancelRun;
}

async function flush(ms = 20) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

let unhandled: unknown[];
const onUnhandled = (reason: unknown) => {
  unhandled.push(reason);
};

beforeEach(() => {
  cancelMock.mockReset().mockResolvedValue(undefined);
  window.sessionStorage.clear();
  unhandled = [];
  process.on("unhandledRejection", onUnhandled);
});

afterEach(() => {
  process.off("unhandledRejection", onUnhandled);
  vi.restoreAllMocks();
});

describe("runs.cancel error containment", () => {
  test("passes arguments through to the SDK unchanged", async () => {
    const client = await freshClient();

    await client.runs.cancel("thread-1", "run-1", false, "interrupt");

    expect(cancelMock).toHaveBeenCalledWith(
      "thread-1",
      "run-1",
      false,
      "interrupt",
    );
  });

  test("terminal-state 409 resolves to not_cancellable instead of rejecting", async () => {
    cancelMock.mockRejectedValue(NOT_CANCELLABLE());
    const client = await freshClient();

    await expect(client.runs.cancel("thread-1", "run-1")).resolves.toEqual({
      status: "not_cancellable",
    });
  });

  test("clears the stale reconnect key for the cancelled run", async () => {
    cancelMock.mockRejectedValue(NOT_CANCELLABLE());
    window.sessionStorage.setItem("lg:stream:thread-1", "run-1");
    const client = await freshClient();

    await client.runs.cancel("thread-1", "run-1");

    expect(window.sessionStorage.getItem("lg:stream:thread-1")).toBeNull();
  });

  test("leaves a newer run's reconnect key untouched", async () => {
    cancelMock.mockRejectedValue(NOT_CANCELLABLE());
    window.sessionStorage.setItem("lg:stream:thread-1", "run-2");
    const client = await freshClient();

    await client.runs.cancel("thread-1", "run-1");

    expect(window.sessionStorage.getItem("lg:stream:thread-1")).toBe("run-2");
  });

  test("no longer rejects on a real cancel failure (the wrapper contains it)", async () => {
    cancelMock.mockRejectedValue(httpError(500, "HTTP 500: boom"));
    const client = await freshClient();

    await expect(
      client.runs.cancel("thread-1", "run-1"),
    ).resolves.toMatchObject({ status: "failed" });
    await flush();
    expect(unhandled).toEqual([]);
  });

  test("does not clear the reconnect key when the cancel fails", async () => {
    cancelMock.mockRejectedValue(httpError(500, "HTTP 500: boom"));
    window.sessionStorage.setItem("lg:stream:thread-1", "run-1");
    const client = await freshClient();

    await client.runs.cancel("thread-1", "run-1");
    expect(window.sessionStorage.getItem("lg:stream:thread-1")).toBe("run-1");
  });
});

describe("cancelRun：可等待的取消结果", () => {
  test("成功 → { status: 'cancelled' }", async () => {
    const cancelRun = await freshCancelRun();

    await expect(cancelRun("thread-1", "run-1")).resolves.toEqual({
      status: "cancelled",
    });
    expect(cancelMock).toHaveBeenCalledWith(
      "thread-1",
      "run-1",
      true,
      "interrupt",
    );
  });

  test("终态 409 → { status: 'not_cancellable' }，并清掉该 run 的 reconnect key", async () => {
    cancelMock.mockRejectedValue(NOT_CANCELLABLE());
    window.sessionStorage.setItem("lg:stream:thread-1", "run-1");
    const cancelRun = await freshCancelRun();

    await expect(cancelRun("thread-1", "run-1")).resolves.toEqual({
      status: "not_cancellable",
    });
    expect(window.sessionStorage.getItem("lg:stream:thread-1")).toBeNull();
  });

  test.each([
    ["HTTP 500", httpError(500, "HTTP 500: boom")],
    ["网络层异常", new TypeError("Failed to fetch")],
  ])(
    "%s → { status: 'failed', error }，不 reject、无 unhandled rejection",
    async (_label, error) => {
      cancelMock.mockRejectedValue(error);
      const cancelRun = await freshCancelRun();

      const outcome = await cancelRun("thread-1", "run-1");

      expect(outcome).toMatchObject({ status: "failed", error });
      await flush();
      expect(unhandled).toEqual([]);
    },
  );

  test.each([
    [
      "another worker 上的 409",
      httpError(
        409,
        "HTTP 409: Run run-1 is not active on this worker and cannot be cancelled",
      ),
    ],
    ["404", httpError(404, "HTTP 404: Run not found")],
  ])("%s 仍是 failed（只有终态 409 才视为已停止）", async (_label, error) => {
    cancelMock.mockRejectedValue(error);
    window.sessionStorage.setItem("lg:stream:thread-1", "run-1");
    const cancelRun = await freshCancelRun();

    const outcome = await cancelRun("thread-1", "run-1");

    expect(outcome).toMatchObject({ status: "failed" });
    expect(window.sessionStorage.getItem("lg:stream:thread-1")).toBe("run-1");
  });

  test("同一 runId 的并发取消合并为一次请求", async () => {
    let release!: () => void;
    cancelMock.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    const cancelRun = await freshCancelRun();

    const first = cancelRun("thread-1", "run-1");
    const second = cancelRun("thread-1", "run-1");
    expect(cancelMock).toHaveBeenCalledTimes(1);
    release();
    await expect(first).resolves.toEqual({ status: "cancelled" });
    await expect(second).resolves.toEqual({ status: "cancelled" });
  });

  test("真实 SDK thread.stop() 路径：cancel 失败也不会成为未处理拒绝", async () => {
    cancelMock.mockRejectedValue(httpError(500, "HTTP 500: boom"));
    const client = await freshClient();

    const handle: { stop: (() => Promise<void>) | null } = { stop: null };
    function Probe() {
      const thread = useStream<Record<string, unknown>>({
        client: client as never,
        assistantId: "lead_agent",
        threadId: "thread-stop",
        fetchStateHistory: false,
        // SDK 只在 reconnectOnMount 下启用 reconnect key；挂载后再写 key，避免触发 rejoin
        reconnectOnMount: true,
      });
      // 渲染期不改外部变量：挂载后由 effect 发布
      useEffect(() => {
        handle.stop = () => thread.stop();
      });
      return null;
    }
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        createElement(
          QueryClientProvider,
          { client: new QueryClient() },
          createElement(Probe, null),
        ),
      );
    });
    // SDK 的 stop 读到 reconnect key 才会发 cancel（浮动 Promise）
    window.sessionStorage.setItem("lg:stream:thread-stop", "run-stop");
    await act(async () => {
      await handle.stop!();
    });
    await flush();
    // SDK 内部按 (threadId, runId) 调 cancel；参数默认值由真实 SDK 方法补齐
    expect(cancelMock.mock.calls[0]?.slice(0, 2)).toEqual([
      "thread-stop",
      "run-stop",
    ]);
    expect(unhandled).toEqual([]);
    await act(async () => root.unmount());
    container.remove();
  });
});

describe("compat 层把中止 rejection 归一成 SDK 认得的形态", () => {
  test("client 的 fetch 把 Chromium 的 abort 消息改写成 AbortError 前缀，AsyncCaller 才不会重试已中止的请求", async () => {
    vi.resetModules();
    await import("@/core/api/api-client");
    const customFetch = clientConfigs.at(-1)?.callerOptions?.fetch;
    expect(customFetch, "client 未注入 callerOptions.fetch").toBeTypeOf(
      "function",
    );

    // Chromium 对 AbortController.abort() 的拒绝形态：name 是 AbortError，但 message 不带前缀
    const chromiumAbort = Object.assign(
      new Error("signal is aborted without reason"),
      { name: "AbortError" },
    );
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(chromiumAbort);

    const normalized = await customFetch!("http://localhost:2024/threads", {
      method: "POST",
      signal: new AbortController().signal,
    }).then(
      () => "resolved",
      (error: Error) => error,
    );
    expect(normalized).toBeInstanceOf(Error);
    expect((normalized as Error).name).toBe("AbortError");
    expect((normalized as Error).message).toMatch(/^AbortError/);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // 非中止错误原样透传
    fetchMock.mockRejectedValueOnce(new Error("Failed to fetch"));
    await expect(
      customFetch!("http://localhost:2024/threads", {}),
    ).rejects.toThrow("Failed to fetch");
    fetchMock.mockRestore();
  });
});
