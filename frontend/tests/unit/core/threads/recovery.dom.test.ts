/**
 * PR3（2026-11-24 前端可靠性批）纵切 B（下半）：停止后的有界同步、取消失败的重试、
 * 断线后的手动重连。用可控的 useStream 替身驱动 hook——isLoading / error / joinStream
 * 由用例直接控制，SDK 回调（onCreated / onError / onFinish）通过传给 useStream 的
 * options 手动触发；run 历史（runs 列表、run-messages 端点）走真实 useThreadHistory。
 */

import type { Run } from "@langchain/langgraph-sdk";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, test, vi } from "vitest";

const h = vi.hoisted(() => ({
  threadId: "t-1",
  thread: {
    isLoading: false,
    error: null as unknown,
    messages: [] as unknown[],
    values: {},
    submit: vi.fn(async () => undefined),
    stop: vi.fn(async () => undefined),
    joinStream: vi.fn(async () => undefined),
  },
  streamOptions: null as Record<string, unknown> | null,
  runs: [] as Array<Partial<Run>>,
  listCalls: 0,
  messageCalls: [] as string[],
  cancelRun: vi.fn(),
  fetchImpl: vi.fn(),
}));

vi.mock("@langchain/langgraph-sdk/react", () => ({
  useStream: (options: Record<string, unknown>) => {
    h.streamOptions = options;
    return { ...h.thread };
  },
}));
vi.mock("@/core/api", () => ({
  getAPIClient: () => ({
    runs: {
      list: () => {
        h.listCalls += 1;
        return Promise.resolve(h.runs);
      },
    },
    threads: { update: async () => ({}) },
  }),
}));
vi.mock("@/core/api/api-client", () => ({ cancelRun: h.cancelRun }));
vi.mock("@/core/api/fetcher", () => ({
  fetch: (input: string) => {
    const runId = /\/runs\/([^/?#]+)\/messages/.exec(String(input))?.[1] ?? "";
    h.messageCalls.push(runId);
    return h.fetchImpl(runId);
  },
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
  getLangGraphBaseURL: () => "",
}));
vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      uploads: { uploadingFiles: "上传中" },
      recovery: {
        stopFailed: (message: string) => `stop failed: ${message}`,
        stopTimeout: (seconds: number) => `stop timed out after ${seconds}s`,
      },
    },
  }),
}));
vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({
    models: [{ name: "m1", supports_thinking: false }],
    modeDefaults: {},
  }),
}));
vi.mock("@/core/tasks/context", () => ({
  useUpdateSubtask: () => () => undefined,
}));
vi.mock("@/core/uploads", () => ({
  promptInputFilePartToFile: vi.fn(),
  uploadFilesWithProgress: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

import { useThreadStream } from "@/core/threads/hooks";
import {
  CANCEL_WAIT_LIMIT_MS,
  classifyStreamError,
} from "@/core/threads/recovery";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

type Api = ReturnType<typeof useThreadStream>;

const captured: { api: Api | null } = { api: null };

const CONTEXT = {
  mode: "standard",
  model_name: "m1",
  reasoning_effort: "low",
} as never;

function Probe() {
  const api = useThreadStream({
    threadId: h.threadId,
    context: CONTEXT,
    onSend: undefined,
    onStart: undefined,
    onSendError: undefined,
    onFinish: undefined,
  });
  // 渲染期不改外部变量：提交后由 effect 发布给用例
  useEffect(() => {
    captured.api = api;
  });
  return null;
}

let root: Root | null = null;
let queryClient: QueryClient | null = null;

async function flush(ms = 20) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

function api(): Api {
  if (!captured.api) throw new Error("hook not mounted");
  return captured.api;
}

/** 第 index 条错误提示的文案（非字符串按空串处理）。 */
function toastErrorText(index: number): string {
  const value = vi.mocked(toast.error).mock.calls[index]?.[0];
  return typeof value === "string" ? value : "";
}

/** run-messages 端点：每个 run 固定一条消息。 */
function okResponse(runId: string) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        data: [
          {
            run_id: runId,
            seq: 1,
            content: { id: `${runId}-m1`, type: "human", content: "hi" },
            metadata: { caller: "user" },
            created_at: "2025-01-01T00:00:00Z",
          },
        ],
        has_more: false,
      }),
  });
}

const run = (runId: string, status: Run["status"]): Partial<Run> => ({
  run_id: runId,
  status,
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
});

async function mount() {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  const element = createElement(
    QueryClientProvider,
    { client: queryClient },
    createElement(Probe),
  );
  await act(async () => {
    root!.render(element);
  });
  await flush();
  await flush();
  return container;
}

/** useStream 替身不会自己通知 React：改完 h.thread 后要显式重渲染一次。 */
async function rerender() {
  // 同一个 element 引用会被 React 跳过：每次新建
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client: queryClient! },
        createElement(Probe),
      ),
    );
  });
  await flush();
}

/** 发一条消息并让 SDK 回 onCreated：得到一个已受理、流在途的 run。 */
async function acceptRun(runId: string) {
  let outcome!: Promise<void>;
  await act(async () => {
    outcome = api().sendMessage("t-1", { text: "A", files: [] });
    void outcome.catch(() => undefined);
    h.thread.isLoading = true;
    (
      h.streamOptions!.onCreated as (meta: {
        run_id: string;
        thread_id: string;
      }) => void
    )({ run_id: runId, thread_id: "t-1" });
  });
  await flush();
  expect(api().activeRunId).toBe(runId);
}

beforeEach(() => {
  h.threadId = "t-1";
  h.thread.isLoading = false;
  h.thread.error = null;
  h.thread.messages = [];
  h.thread.submit.mockClear();
  h.thread.stop.mockClear().mockImplementation(async () => {
    // SDK 的 stop 中止本地流：随后 isLoading 回落
    h.thread.isLoading = false;
  });
  h.thread.joinStream.mockClear().mockImplementation(async () => {
    h.thread.isLoading = true;
  });
  h.streamOptions = null;
  h.runs = [];
  h.listCalls = 0;
  h.messageCalls.length = 0;
  h.cancelRun.mockReset();
  h.cancelRun.mockResolvedValue({ status: "cancelled" });
  h.fetchImpl.mockReset();
  h.fetchImpl.mockImplementation((runId: string) => okResponse(runId));
  vi.mocked(toast.error).mockClear();
  vi.mocked(toast.success).mockClear();
  window.sessionStorage.clear();
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = null;
  queryClient = null;
  captured.api = null;
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("classifyStreamError", () => {
  it("连接类错误 → connection；run 不存在（404）→ stale_run；业务 4xx → business；中止 → aborted", () => {
    expect(classifyStreamError(new TypeError("Failed to fetch"))).toBe(
      "connection",
    );
    expect(classifyStreamError(new Error("network error"))).toBe("connection");
    expect(classifyStreamError(new Error("Load failed"))).toBe("connection");
    expect(
      classifyStreamError(
        Object.assign(new Error("Run not found"), { status: 404 }),
      ),
    ).toBe("stale_run");
    expect(classifyStreamError(new Error("HTTP 404: Run x not found"))).toBe(
      "stale_run",
    );
    expect(
      classifyStreamError(
        Object.assign(new Error("Validation failed"), { status: 422 }),
      ),
    ).toBe("business");
    // 别的资源的 "not found" 不是 stale run：不能静默吞掉、也不能清掉 rejoin key
    expect(classifyStreamError(new Error("Thread not found"))).toBe("business");
    expect(classifyStreamError(new Error("Agent not found"))).toBe("business");
    // run 自己没出问题、是它里面的工具 / agent 找不到：同样是业务错误，失败原因要给用户看
    expect(
      classifyStreamError(new Error("Run failed: Tool 'x' not found")),
    ).toBe("business");
    expect(classifyStreamError(new Error("Run error: agent not found"))).toBe(
      "business",
    );
    // 通用词不算连接类错误：后端业务错误不该弹出重连入口
    expect(classifyStreamError(new Error("Tool unavailable"))).toBe("business");
    expect(classifyStreamError(new Error("Run terminated by policy"))).toBe(
      "business",
    );
    expect(
      classifyStreamError(new Error("HTTP 503: Service Unavailable")),
    ).toBe("connection");
    // 没有当前 run 时的裸 404 是业务错误（线程 / 页面不存在），不是「run 已不存在」
    expect(
      classifyStreamError(
        Object.assign(new Error("Not Found"), { status: 404 }),
        { hasRun: false },
      ),
    ).toBe("business");
    expect(
      classifyStreamError(
        Object.assign(new Error("Not Found"), { status: 404 }),
        { hasRun: true },
      ),
    ).toBe("stale_run");
    // HTTP 404 文本形态只在有 run 时才算 stale
    expect(
      classifyStreamError(new Error("HTTP 404: Not Found"), { hasRun: false }),
    ).toBe("business");
    expect(classifyStreamError("Request failed.")).toBe("business");
    const aborted = new Error("The operation was aborted");
    aborted.name = "AbortError";
    expect(classifyStreamError(aborted)).toBe("aborted");
  });
});

describe("停止后的同步（CancelOutcome）", () => {
  test("cancel 受理 → 有界刷新 runs 与当前分片：running → interrupted", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    expect(h.messageCalls).toEqual(["run-1"]);
    await acceptRun("run-1");

    // 服务端把 run 置为终态后 cancel 才返回
    h.runs = [run("run-1", "interrupted")];
    const listCallsBefore = h.listCalls;
    await act(async () => {
      await api().stop();
    });
    await flush();

    expect(h.cancelRun).toHaveBeenCalledWith("t-1", "run-1");
    expect(h.listCalls).toBe(listCallsBefore + 1);
    expect(h.messageCalls).toEqual(["run-1", "run-1"]);
    expect(api().runsById["run-1"]?.status).toBe("interrupted");
    expect(api().cancelFailed).toBe(false);
    expect(toast.error).not.toHaveBeenCalled();
  });

  test("cancel 500 / 网络失败：无 unhandled rejection、不刷新成 interrupted、出现可重试错误；重试即再次调用同一 cancel", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    await acceptRun("run-1");
    h.cancelRun.mockResolvedValue({
      status: "failed",
      error: new Error("HTTP 500: boom"),
    });

    await act(async () => {
      await api().stop();
    });
    await flush();

    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(toastErrorText(0)).toMatch(/stop failed/);
    expect(api().cancelFailed).toBe(true);
    // 不伪造终态：没有刷新，分片仍是加载时的状态
    expect(h.messageCalls).toEqual(["run-1"]);
    expect(api().runsById["run-1"]?.status).toBe("running");

    // 重试：服务端这次受理了
    h.runs = [run("run-1", "interrupted")];
    h.cancelRun.mockResolvedValue({ status: "cancelled" });
    await act(async () => {
      await api().retryCancel();
    });
    await flush();

    expect(h.cancelRun).toHaveBeenCalledTimes(2);
    expect(h.cancelRun).toHaveBeenLastCalledWith("t-1", "run-1");
    expect(api().cancelFailed).toBe(false);
    expect(h.messageCalls).toEqual(["run-1", "run-1"]);
    expect(api().runsById["run-1"]?.status).toBe("interrupted");
  });

  test("cancel 请求挂死：等待有上限，超时按失败结案并释放 busy", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    await acceptRun("run-1");
    h.cancelRun.mockImplementation(() => new Promise(() => undefined));
    // 假计时器只在推进等待上限时打开：挂载与受理都走真实计时
    vi.useFakeTimers();
    try {
      await act(async () => {
        const stopped = api().stop();
        await vi.advanceTimersByTimeAsync(CANCEL_WAIT_LIMIT_MS + 1);
        await stopped;
      });

      expect(api().cancelFailed).toBe(true);
      expect(toast.error).toHaveBeenCalledTimes(1);
      // 超时提示不再被「停止失败」包一层
      expect(toastErrorText(0)).toBe("stop timed out after 30s");
      // busy 已释放：SDK 流早已收尾，下一次发送照常入队，不再被判 busy
      let rejected: unknown = null;
      await act(async () => {
        void api()
          .sendMessage("t-1", { text: "B", files: [] })
          .catch((error: unknown) => {
            rejected = error;
          });
      });
      expect(rejected).toBeNull();
      expect(h.thread.submit).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  test("not_cancellable（终态 409）：按已停止处理，不算失败；同样释放当前 run 并做一次有界刷新", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    await acceptRun("run-1");
    h.runs = [
      { ...run("run-1", "success"), updated_at: "2025-01-02T00:00:00Z" },
    ];
    h.cancelRun.mockResolvedValue({ status: "not_cancellable" });

    await act(async () => {
      await api().stop();
    });
    await flush();

    expect(api().cancelFailed).toBe(false);
    expect(toast.error).not.toHaveBeenCalled();
    // run 已是终态：不再是「当前 run」，也不再给重连入口
    expect(api().activeRunId).toBeNull();
    expect(h.listCalls).toBe(2);
    expect(h.messageCalls).toEqual(["run-1", "run-1"]);
  });

  test("重试停止只对仍是当前线程 / 当前 run 的失败生效", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    await acceptRun("run-1");
    h.cancelRun.mockResolvedValue({
      status: "failed",
      error: new Error("HTTP 500: boom"),
    });
    await act(async () => {
      await api().stop();
    });
    await flush();
    expect(api().cancelFailed).toBe(true);

    // 另一个 run 成为当前 run（或切了线程）：旧失败不再属于它
    await act(async () => {
      h.thread.isLoading = true;
      (
        h.streamOptions!.onCreated as (meta: {
          run_id: string;
          thread_id: string;
        }) => void
      )({ run_id: "run-2", thread_id: "t-1" });
    });
    await flush();
    expect(api().cancelFailed).toBe(false);
    await act(async () => {
      await api().retryCancel();
    });
    expect(h.cancelRun).toHaveBeenCalledTimes(1);
  });

  test("停止失败 / 超时的提示走 i18n 文案；重试停止在途时按钮态为真", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    await acceptRun("run-1");
    h.cancelRun.mockResolvedValue({
      status: "failed",
      error: new Error("HTTP 500: boom"),
    });
    await act(async () => {
      await api().stop();
    });
    await flush();
    expect(toastErrorText(0)).toBe("stop failed: HTTP 500: boom");

    let release!: (value: unknown) => void;
    h.cancelRun.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    let retrying: Promise<void> | null = null;
    await act(async () => {
      retrying = api().retryCancel();
    });
    expect(api().isCancelling).toBe(true);
    await act(async () => {
      release({ status: "cancelled" });
      await retrying;
    });
    await flush();
    expect(api().isCancelling).toBe(false);
  });
});

describe("断线后的恢复 owner", () => {
  test("连接类错误 + run 仍在服务端运行：显示重连入口；重连单飞，join resolve 不等于恢复成功", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    await acceptRun("run-1");

    // 流断了：SDK 以连接类错误收尾
    await act(async () => {
      h.thread.error = new TypeError("Failed to fetch");
      h.thread.isLoading = false;
    });
    await rerender();
    expect(api().isRecoverableDisconnect).toBe(true);

    await act(async () => {
      // 连点两次：只 join 一次
      await Promise.all([
        api().reconnectActiveRun(),
        api().reconnectActiveRun(),
      ]);
    });
    expect(h.thread.joinStream).toHaveBeenCalledTimes(1);
    expect(h.thread.joinStream).toHaveBeenCalledWith("run-1");
    expect(api().isReconnecting).toBe(false);
    // join resolve 不等于恢复成功：没有任何成功提示
    expect(toast.success).not.toHaveBeenCalled();

    // 连接结果由 onFinish / onError 驱动：流真正恢复（error 清空、流收尾）后入口才收起
    await act(async () => {
      h.thread.error = null;
      h.thread.isLoading = false;
    });
    await rerender();
    expect(api().isRecoverableDisconnect).toBe(false);
  });

  test("mount 自动 rejoin 未结束（isLoading）时手动重连不二次 join", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    await acceptRun("run-1");
    await act(async () => {
      h.thread.error = new TypeError("Failed to fetch");
      h.thread.isLoading = false;
    });
    await rerender();
    expect(api().isRecoverableDisconnect).toBe(true);

    await act(async () => {
      h.thread.isLoading = true; // SDK 正在自动 rejoin
    });
    await rerender();
    await act(async () => {
      await api().reconnectActiveRun();
    });
    expect(h.thread.joinStream).not.toHaveBeenCalled();
  });

  test("没有 runId、或业务 4xx / 已结束的 run：不显示重连入口", async () => {
    await mount();
    await act(async () => {
      h.thread.error = new TypeError("Failed to fetch");
      h.thread.isLoading = false;
    });
    await rerender();
    expect(api().isRecoverableDisconnect).toBe(false);

    await acceptRun("run-1");
    await act(async () => {
      h.thread.error = Object.assign(new Error("Validation failed"), {
        status: 422,
      });
      h.thread.isLoading = false;
    });
    await rerender();
    expect(api().isRecoverableDisconnect).toBe(false);

    // run 在服务端已是终态：没有可恢复的东西；断线期间产生的尾段由一次补取补齐
    h.runs = [run("run-1", "success")];
    await act(async () => {
      h.thread.error = new TypeError("Failed to fetch");
      await queryClient!.invalidateQueries({ queryKey: ["thread", "t-1"] });
    });
    await rerender();
    await flush();
    expect(api().isRecoverableDisconnect).toBe(false);
    // runs 列表里此前没有这个 run：得知终态后补取一次
    expect(h.messageCalls).toEqual(["run-1"]);
  });

  test("mount 自动 rejoin 的 run（reconnect key）归属当前流：断线后给重连入口，停止同样等服务端确认", async () => {
    h.runs = [run("run-live", "running")];
    // SDK reconnectOnMount 读这个键接续流，不经过 onCreated
    window.sessionStorage.setItem("lg:stream:t-1", "run-live");
    await mount();
    expect(api().activeRunId).toBe("run-live");

    await act(async () => {
      h.thread.error = new TypeError("Failed to fetch");
      h.thread.isLoading = false;
    });
    await rerender();
    expect(api().isRecoverableDisconnect).toBe(true);

    // 用户停止：rejoin 的 run 没有对应的 submission，也要发显式取消并做有界刷新
    h.runs = [run("run-live", "interrupted")];
    await act(async () => {
      await api().stop();
    });
    await flush();
    expect(h.cancelRun).toHaveBeenCalledWith("t-1", "run-live");
    expect(api().runsById["run-live"]?.status).toBe("interrupted");
    expect(api().cancelFailed).toBe(false);
    expect(toast.error).not.toHaveBeenCalled();
  });

  test("run 正常收尾后 activeRunId 回到 null：stop() 不再对已结束的 run 发 cancel", async () => {
    h.runs = [run("run-1", "success")];
    await mount();
    await acceptRun("run-1");
    h.runs = [
      { ...run("run-1", "success"), updated_at: "2025-01-02T00:00:00Z" },
    ];

    // SDK onFinish（带 meta.run_id）
    await act(async () => {
      h.thread.isLoading = false;
      (
        h.streamOptions!.onFinish as (
          state: unknown,
          meta?: { run_id?: string; thread_id?: string },
        ) => void
      )({ values: { messages: [] } }, { run_id: "run-1", thread_id: "t-1" });
    });
    await flush();
    expect(api().activeRunId).toBeNull();

    await act(async () => {
      await api().stop();
    });
    await flush();
    expect(h.cancelRun).not.toHaveBeenCalled();
  });

  test("run 在切线程期间收尾、再切回：activeRunId 不指向已结束的旧 run", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    await acceptRun("run-1");

    // 切到 t-2：旧线程的流此时收尾（onFinish 的 meta 属于 t-1，清理条件不满足）
    await act(async () => {
      h.threadId = "t-2";
    });
    await rerender();
    await act(async () => {
      (
        h.streamOptions!.onFinish as (
          state: unknown,
          meta?: { run_id?: string; thread_id?: string },
        ) => void
      )({ values: { messages: [] } }, { run_id: "run-1", thread_id: "t-1" });
    });
    // 切回 t-1
    h.runs = [run("run-1", "success")];
    await act(async () => {
      h.threadId = "t-1";
    });
    await rerender();
    await flush();
    expect(api().activeRunId).toBeNull();
    expect(api().isRecoverableDisconnect).toBe(false);
  });

  test("迟到的 stale 回调属于别的线程：不删当前线程的 rejoin key", async () => {
    h.runs = [run("run-live", "running")];
    window.sessionStorage.setItem("lg:stream:t-1", "run-live");
    await mount();
    expect(api().activeRunId).toBe("run-live");

    await act(async () => {
      (
        h.streamOptions!.onError as (
          error: unknown,
          meta?: { run_id?: string; thread_id?: string },
        ) => void
      )(Object.assign(new Error("Run run-old not found"), { status: 404 }), {
        run_id: "run-old",
        thread_id: "t-other",
      });
    });
    await flush();
    expect(window.sessionStorage.getItem("lg:stream:t-1")).toBe("run-live");
    expect(api().isRecoverableDisconnect).toBe(false);
  });

  test("loadMoreHistory 失败不产生未处理拒绝：入口仍可重试", async () => {
    h.runs = [run("run-2", "success"), run("run-1", "success")];
    await mount();
    const goodResponder = h.fetchImpl.getMockImplementation()!;
    let fail = true;
    h.fetchImpl.mockImplementation((runId: string) => {
      if (runId === "run-1" && fail) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve({ detail: "unavailable" }),
        });
      }
      return goodResponder(runId);
    });
    let rejected: unknown = "not rejected";
    await act(async () => {
      await api()
        .loadMoreHistory()
        .catch((error: unknown) => {
          rejected = error;
        });
    });
    await flush();
    expect(rejected).toBe("not rejected");
    expect(api().hasMoreHistory).toBe(true);
    fail = false;
    await act(async () => {
      await api().loadMoreHistory();
    });
    await flush();
    expect(api().hasMoreHistory).toBe(false);
  });

  test("stale run 404：清 reconnect key 并失效 history / runs，不提示", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    await acceptRun("run-1");
    window.sessionStorage.setItem("lg:stream:t-1", "run-1");
    const listCallsBefore = h.listCalls;

    await act(async () => {
      (
        h.streamOptions!.onError as (
          error: unknown,
          meta?: { run_id?: string; thread_id?: string },
        ) => void
      )(Object.assign(new Error("Run run-1 not found"), { status: 404 }), {
        run_id: "run-1",
        thread_id: "t-1",
      });
    });
    await flush();

    expect(window.sessionStorage.getItem("lg:stream:t-1")).toBeNull();
    expect(h.listCalls).toBe(listCallsBefore + 1);
    expect(toast.error).not.toHaveBeenCalled();
    // key 已清：即便 runs 列表深度相等（structural sharing 不会换引用），也不再是当前 run
    expect(api().activeRunId).toBeNull();
  });

  test("join 立即失败也会释放单飞记录：再次重连照常发起", async () => {
    h.runs = [run("run-1", "running")];
    await mount();
    await acceptRun("run-1");
    await act(async () => {
      h.thread.error = new TypeError("Failed to fetch");
      h.thread.isLoading = false;
    });
    await rerender();
    expect(api().isRecoverableDisconnect).toBe(true);

    h.thread.joinStream.mockImplementation(async () => {
      throw new Error("HTTP 409: run is not joinable");
    });
    await act(async () => {
      await api().reconnectActiveRun();
    });
    expect(h.thread.joinStream).toHaveBeenCalledTimes(1);
    expect(toastErrorText(0)).toContain("not joinable");

    h.thread.joinStream.mockImplementation(async () => {
      h.thread.isLoading = true;
    });
    await act(async () => {
      await api().reconnectActiveRun();
    });
    expect(h.thread.joinStream).toHaveBeenCalledTimes(2);
  });
});
