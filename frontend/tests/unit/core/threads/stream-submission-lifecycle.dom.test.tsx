/**
 * PR2（2026-11-24 前端可靠性批）：useThreadStream 的受理 / 完成双生命周期。
 *
 * 真实 SDK `useStream` + 可控 fake client（写法同 stream-isloading-at-submit）：
 * `runs.stream` 何时触发 `onRunCreated`（= SDK onCreated）由用例决定，流何时结束、
 * 何时抛 error 也由用例决定。`thread.submit()` 本身只入队即返回，所以
 * `sendMessage()` 的 Promise 必须等到对应 submission 的 onCreated 才 resolve。
 *
 * - 受理前失败 / 停止 → accepted 以 SendRejectedError(kind) reject，输入保留
 * - SDK 未收尾（thread.isLoading）时的提交按 busy 拒绝，不提示
 * - 回调归属：onCreated / 创建前 onError / onStop 归当前 submission；onCreated 之后按 run_id
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactElement, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { LocalSettings } from "@/core/settings";
import { useThreadStream } from "@/core/threads/hooks";
import type { AgentThreadState } from "@/core/threads/types";

const h = vi.hoisted(() => {
  type StreamEvent = { event: string; data: unknown };
  type RunCreated = { run_id: string; thread_id?: string };
  type StreamOptions = {
    signal?: AbortSignal;
    context?: Record<string, unknown>;
    onRunCreated?: (meta: RunCreated) => void;
  };
  type Entry = {
    threadId: string;
    options: StreamOptions;
    ctrl: ReturnType<typeof createControllableStream>;
    created: boolean;
  };

  function createControllableStream(signal?: AbortSignal) {
    const buffer: StreamEvent[] = [];
    let notify: (() => void) | null = null;
    let rejectWait: ((error: unknown) => void) | null = null;
    let ended = false;
    let abortError: Error | null = null;
    const iterable: AsyncIterable<StreamEvent> = {
      [Symbol.asyncIterator]() {
        return {
          async next(): Promise<IteratorResult<StreamEvent>> {
            const pendingAbort = abortError;
            if (pendingAbort) throw pendingAbort;
            while (buffer.length === 0 && !ended) {
              await new Promise<void>((resolve, reject) => {
                notify = resolve;
                rejectWait = reject;
              });
              notify = null;
              rejectWait = null;
              const waitAbort = abortError;
              if (waitAbort) throw waitAbort;
            }
            if (buffer.length > 0) {
              return { value: buffer.shift()!, done: false };
            }
            return { value: undefined, done: true };
          },
        };
      },
    };
    // 真实 fetch 流在 signal 中止时抛 AbortError（SDK 据此静默收尾）
    signal?.addEventListener("abort", () => {
      abortError = Object.assign(new Error("The operation was aborted"), {
        name: "AbortError",
      });
      rejectWait?.(abortError);
      notify?.();
    });
    return {
      iterable,
      push(ev: StreamEvent) {
        buffer.push(ev);
        notify?.();
      },
      end() {
        ended = true;
        notify?.();
      },
    };
  }

  const streams: Entry[] = [];
  const joins: Entry[] = [];
  const upload = vi.fn();
  const convert = vi.fn(
    async (part: { file?: File } | undefined) => part?.file ?? null,
  );
  const stream = vi.fn(
    async (threadId: string, _assistantId: string, options: StreamOptions) => {
      const entry: Entry = {
        threadId,
        options,
        ctrl: createControllableStream(options.signal),
        created: false,
      };
      streams.push(entry);
      return entry.ctrl.iterable;
    },
  );
  const cancel = vi.fn(async () => ({}));
  /** hook 调用的可等待取消（真实实现在 core/api/api-client，另有单测） */
  const cancelRun = vi.fn();
  const client = {
    threads: {
      create: vi.fn(),
      getHistory: async () => history,
      getState: async () => ({ checkpoint: null, values: {} }),
      update: async () => ({}),
      updateState: async () => ({}),
    },
    runs: {
      stream,
      cancel,
      list: async () => [],
      joinStream: async (threadId: string, runId: string) => {
        const entry: Entry = {
          threadId,
          options: {},
          ctrl: createControllableStream(),
          created: true,
        };
        void runId;
        joins.push(entry);
        return entry.ctrl.iterable;
      },
    },
  };
  const history: Array<Record<string, unknown>> = [];
  return {
    streams,
    joins,
    upload,
    convert,
    stream,
    cancel,
    cancelRun,
    client,
    history,
  };
});

vi.mock("@/core/api", () => ({ getAPIClient: () => h.client }));
vi.mock("@/core/api/api-client", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  cancelRun: h.cancelRun,
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
  getLangGraphBaseURL: () => "",
}));
vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: { uploads: { uploadingFiles: "上传中" } } }),
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
  promptInputFilePartToFile: h.convert,
  uploadFilesWithProgress: h.upload,
}));
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

const toastError = vi.mocked(toast.error);

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

type Context = LocalSettings["context"];
type Callbacks = {
  onSend: ReturnType<typeof vi.fn<(threadId: string) => void>>;
  onStart: ReturnType<typeof vi.fn<(threadId: string, runId: string) => void>>;
  onSendError: ReturnType<
    typeof vi.fn<(threadId: string, opts: { wasNewThread: boolean }) => void>
  >;
  onFinish: ReturnType<typeof vi.fn<(state: AgentThreadState) => void>>;
};

type Api = ReturnType<typeof useThreadStream>;

let container: HTMLDivElement | null = null;
let root: Root | null = null;
let queryClient: QueryClient | null = null;
const captured: { api: Api | null } = { api: null };

function Probe({
  threadId,
  context,
  callbacks,
}: {
  threadId: string | undefined;
  context: Context;
  callbacks: Callbacks;
}) {
  const api = useThreadStream({
    threadId,
    context,
    onSend: callbacks.onSend,
    onStart: callbacks.onStart,
    onSendError: callbacks.onSendError,
    onFinish: callbacks.onFinish,
  });
  // 渲染期不改外部变量：提交后由 effect 发布给用例
  useEffect(() => {
    captured.api = api;
  });
  return (
    <output data-testid="is-loading">{String(api.thread.isLoading)}</output>
  );
}

function callbacks(): Callbacks {
  return {
    onSend: vi.fn(),
    onStart: vi.fn(),
    onSendError: vi.fn(),
    onFinish: vi.fn(),
  };
}

const CONTEXT: Context = {
  mode: "standard",
  model_name: "m1",
  reasoning_effort: "low",
} as Context;

function render(threadId: string | undefined, cb: Callbacks) {
  return act(async () => {
    root!.render(
      (
        <QueryClientProvider client={queryClient!}>
          <Probe threadId={threadId} context={CONTEXT} callbacks={cb} />
        </QueryClientProvider>
      ) as ReactElement,
    );
  });
}

async function mount(threadId: string | undefined, cb = callbacks()) {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await render(threadId, cb);
  await flush();
  return cb;
}

async function flush(ms = 30) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

function api(): Api {
  if (!captured.api) throw new Error("hook not mounted");
  return captured.api;
}

function textOf(message: { content?: unknown }): string {
  const content = message.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        typeof part === "object" && part && "text" in part
          ? String(part.text)
          : "",
      )
      .join("");
  }
  return "";
}

function hasOptimistic(text: string): boolean {
  return api().thread.messages.some((m) => textOf(m) === text);
}

/** 触发第 index 条流的 onRunCreated（= SDK onCreated）。 */
function fireCreated(index: number, runId: string, threadId?: string) {
  const entry = h.streams[index];
  if (!entry) throw new Error(`stream ${index} not started`);
  entry.created = true;
  entry.options.onRunCreated?.({
    run_id: runId,
    thread_id: threadId ?? entry.threadId,
  });
}

const ATTACHMENT = {
  type: "file" as const,
  file: new File(["x"], "a.txt"),
  url: "blob:a",
  mediaType: "text/plain",
  filename: "a.txt",
};

beforeEach(() => {
  h.streams.length = 0;
  h.joins.length = 0;
  h.history.length = 0;
  h.upload.mockReset();
  h.upload.mockResolvedValue({ files: [] });
  h.convert.mockReset();
  h.convert.mockImplementation((part) => Promise.resolve(part?.file ?? null));
  h.stream.mockClear();
  h.cancel.mockClear();
  h.cancel.mockResolvedValue({});
  h.cancelRun.mockReset();
  h.cancelRun.mockResolvedValue({ status: "cancelled" });
  h.client.threads.create.mockReset();
  h.client.threads.create.mockImplementation(async (input) => ({
    thread_id: input?.threadId ?? "t-created",
  }));
  toastError.mockClear();
  window.sessionStorage.clear();
});

afterEach(async () => {
  for (const entry of [...h.streams, ...h.joins]) {
    if (!entry.options.signal?.aborted) entry.ctrl.end();
  }
  await flush(10);
  if (root) {
    await act(async () => root!.unmount());
  }
  container?.remove();
  container = null;
  root = null;
  queryClient = null;
  captured.api = null;
});

describe("受理契约：accepted 以 onCreated 为准", () => {
  test("thread.submit() 只入队；sendMessage 的 Promise 等到对应 onCreated 才 resolve", async () => {
    const cb = await mount("t-1");
    let settled: "pending" | "resolved" | "rejected" = "pending";
    const sent = api()
      .sendMessage("t-1", { text: "hi", files: [] })
      .then(
        () => {
          settled = "resolved";
        },
        () => {
          settled = "rejected";
        },
      );

    await flush();
    expect(h.stream).toHaveBeenCalledTimes(1);
    expect(settled).toBe("pending");
    expect(cb.onSend).toHaveBeenCalledWith("t-1");
    expect(cb.onStart).not.toHaveBeenCalled();

    fireCreated(0, "run-1");
    await act(async () => {
      await sent;
    });
    expect(settled).toBe("resolved");
    expect(cb.onStart).toHaveBeenCalledTimes(1);
    expect(cb.onStart).toHaveBeenCalledWith("t-1", "run-1");
    expect(h.stream.mock.calls[0]?.[2]?.context).toMatchObject({
      mode: "standard",
      thread_id: "t-1",
    });
  });

  test("创建前失败 → accepted 以 failed reject，onSendError 回传新线程标记，toast 恰好一次，optimistic 清理", async () => {
    const cb = await mount(undefined);
    h.client.threads.create.mockRejectedValueOnce(
      new Error("thread create 500"),
    );

    await expect(
      api().sendMessage("t-new", { text: "hi", files: [] }),
    ).rejects.toMatchObject({ kind: "failed" });

    expect(cb.onSend).toHaveBeenCalledWith("t-new");
    expect(cb.onSendError).toHaveBeenCalledTimes(1);
    expect(cb.onSendError).toHaveBeenCalledWith("t-new", {
      wasNewThread: true,
    });
    expect(cb.onStart).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledTimes(1);
    const firstToast = toastError.mock.calls[0]?.[0];
    expect(typeof firstToast === "string" ? firstToast : "").toMatch(
      /thread create 500/,
    );
    expect(hasOptimistic("hi")).toBe(false);
  });

  test("已有线程创建前失败 → onSendError 的 wasNewThread 为 false", async () => {
    const cb = await mount("t-1");
    h.stream.mockRejectedValueOnce(new Error("runs.stream 503"));

    await expect(
      api().sendMessage("t-1", { text: "hi", files: [] }),
    ).rejects.toMatchObject({ kind: "failed" });

    expect(cb.onSendError).toHaveBeenCalledWith("t-1", { wasNewThread: false });
    expect(toastError).toHaveBeenCalledTimes(1);
  });

  test("受理前停止 → accepted 以 stopped reject、不发 cancel、不提示；B 重试只归属 B", async () => {
    const cb = await mount("t-1");
    const rejection = api()
      .sendMessage("t-1", { text: "A", files: [] })
      .then(
        () => "resolved",
        (error: unknown) => error,
      );
    await flush();
    expect(h.stream).toHaveBeenCalledTimes(1);

    await act(async () => {
      await api().stop();
    });
    expect(await rejection).toMatchObject({ kind: "stopped" });
    expect(h.cancel).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
    expect(cb.onStart).not.toHaveBeenCalled();
    expect(hasOptimistic("A")).toBe(false);

    // SDK 收尾（isLoading 回落）后同线程重试 B
    await flush();
    expect(api().thread.isLoading).toBe(false);
    const sentB = api().sendMessage("t-1", { text: "B", files: [] });
    await flush();
    expect(h.stream).toHaveBeenCalledTimes(2);
    expect(h.streams[0]!.created).toBe(false);
    fireCreated(1, "run-2");
    await act(async () => {
      await sentB;
    });
    expect(cb.onStart).toHaveBeenCalledTimes(1);
    expect(cb.onStart).toHaveBeenLastCalledWith("t-1", "run-2");
    expect(toastError).not.toHaveBeenCalled();
    expect(cb.onSendError).toHaveBeenCalledTimes(1);
  });

  test("SDK 未收尾时的提交按 busy 拒绝：保留输入、不提示、不再入队", async () => {
    await mount("t-1");
    const sentA = api().sendMessage("t-1", { text: "A", files: [] });
    await flush();
    expect(api().thread.isLoading).toBe(true);

    await expect(
      api().sendMessage("t-1", { text: "B", files: [] }),
    ).rejects.toMatchObject({ kind: "busy" });

    expect(toastError).not.toHaveBeenCalled();
    expect(h.stream).toHaveBeenCalledTimes(1);
    expect(hasOptimistic("B")).toBe(false);

    fireCreated(0, "run-1");
    await act(async () => {
      await sentA;
    });
  });

  test("受理后停止：之后同一 run 的迟到 finish / error 走既有路径，不重复清理、不抛", async () => {
    const cb = await mount("t-1");
    const sent = api().sendMessage("t-1", { text: "A", files: [] });
    await flush();
    fireCreated(0, "run-1");
    await act(async () => {
      await sent;
    });

    await act(async () => {
      await api().stop();
    });
    expect(toastError).not.toHaveBeenCalled();

    // 迟到的同 run 回调（rejoin 形态）：finish 照常通知页面
    h.history.push({ values: { messages: [] } });
    await act(async () => {
      await api().thread.joinStream("run-1");
    });
    h.joins[0]!.ctrl.end();
    await flush();
    expect(cb.onFinish).toHaveBeenCalledTimes(1);
    expect(toastError).not.toHaveBeenCalled();

    // 迟到的同 run error：按既有路径提示一次，不抛、不重复
    await act(async () => {
      await api().thread.joinStream("run-1");
    });
    h.joins[1]!.ctrl.push({ event: "error", data: { message: "late error" } });
    await flush();
    expect(toastError).toHaveBeenCalledTimes(1);
  });
});

describe("上传会话与 onSend 时机", () => {
  test("onSend 在上传完成后、提交 run 前触发", async () => {
    const cb = await mount("t-1");
    let release!: (value: { files: [] }) => void;
    h.upload.mockImplementationOnce(
      () =>
        new Promise<{ files: [] }>((resolve) => {
          release = resolve;
        }),
    );
    const sent = api().sendMessage("t-1", {
      text: "带附件",
      files: [ATTACHMENT],
    });
    await flush();
    expect(h.upload).toHaveBeenCalledTimes(1);
    expect(cb.onSend).not.toHaveBeenCalled();
    expect(h.stream).not.toHaveBeenCalled();
    expect(api().isUploading).toBe(true);

    release({ files: [] });
    await flush();
    expect(cb.onSend).toHaveBeenCalledWith("t-1");
    expect(h.stream).toHaveBeenCalledTimes(1);
    expect(api().isUploading).toBe(false);

    fireCreated(0, "run-1");
    await act(async () => {
      await sent;
    });
  });

  test("A 上传中切到线程 B：abort A 的上传，A 以 stopped reject，B 不被污染", async () => {
    const cb = await mount("t-1");
    const signals: Array<AbortSignal | undefined> = [];
    h.upload.mockImplementation(
      (
        _threadId: string,
        _files: File[],
        _onProgress: undefined,
        options?: { signal?: AbortSignal },
      ) =>
        new Promise<{ files: [] }>(() => {
          signals.push(options?.signal);
        }),
    );
    const rejection = api()
      .sendMessage("t-1", { text: "A", files: [ATTACHMENT] })
      .then(
        () => "resolved",
        (error: unknown) => error,
      );
    await flush();
    expect(signals[0]?.aborted).toBe(false);

    await render("t-2", cb);
    await flush();
    expect(await rejection).toMatchObject({ kind: "stopped" });
    expect(signals[0]?.aborted).toBe(true);
    expect(h.stream).not.toHaveBeenCalled();
    expect(cb.onSend).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();

    // B 正常发送不受 A 影响
    h.upload.mockResolvedValue({ files: [] });
    const sentB = api().sendMessage("t-2", { text: "B", files: [] });
    await flush();
    expect(h.stream).toHaveBeenCalledTimes(1);
    expect(h.stream.mock.calls[0]?.[0]).toBe("t-2");
    fireCreated(0, "run-b", "t-2");
    await act(async () => {
      await sentB;
    });
    expect(cb.onSend).toHaveBeenCalledTimes(1);
    expect(cb.onSend).toHaveBeenCalledWith("t-2");
  });

  test("附件 URL fallback 转换中停止：不进入上传与 submit", async () => {
    const cb = await mount("t-1");
    let release!: (file: File | null) => void;
    h.convert.mockImplementationOnce(
      () =>
        new Promise<File | null>((resolve) => {
          release = resolve;
        }),
    );
    const rejection = api()
      .sendMessage("t-1", {
        text: "A",
        files: [
          {
            type: "file",
            url: "blob:a",
            mediaType: "text/plain",
            filename: "a.txt",
          },
        ],
      })
      .then(
        () => "resolved",
        (error: unknown) => error,
      );
    await flush();
    await act(async () => {
      await api().stop();
    });
    release(new File(["x"], "a.txt"));
    await flush();
    expect(await rejection).toMatchObject({ kind: "stopped" });
    expect(h.upload).not.toHaveBeenCalled();
    expect(h.stream).not.toHaveBeenCalled();
    expect(cb.onSend).not.toHaveBeenCalled();
  });
});

describe("可等待的取消（纵切 C）", () => {
  /** 受理后的 submission（run-1）。 */
  async function acceptedRun() {
    const cb = await mount("t-1");
    const sent = api().sendMessage("t-1", { text: "A", files: [] });
    await flush();
    fireCreated(0, "run-1");
    await act(async () => {
      await sent;
    });
    return cb;
  }

  test("受理后停止且 cancel 受理：对 (thread, run) 发 cancel，不提示", async () => {
    await acceptedRun();
    await act(async () => {
      await api().stop();
    });
    expect(h.cancelRun).toHaveBeenCalledWith("t-1", "run-1");
    expect(toastError).not.toHaveBeenCalled();
  });

  test("cancel 失败：toast 恰好一次并带真实错误，不伪造流错误", async () => {
    await acceptedRun();
    h.cancelRun.mockResolvedValueOnce({
      status: "failed",
      error: new Error("HTTP 500: cancel boom"),
    });
    await act(async () => {
      await api().stop();
    });
    await flush();
    expect(toastError).toHaveBeenCalledTimes(1);
    const first = toastError.mock.calls[0]?.[0];
    expect(typeof first === "string" ? first : "").toMatch(/cancel boom/);
    expect(api().thread.error ?? null).toBeNull();
  });

  test("终态 409（not_cancellable）：维持兼容语义——视为已停止，不提示", async () => {
    await acceptedRun();
    h.cancelRun.mockResolvedValueOnce({ status: "not_cancellable" });
    await act(async () => {
      await api().stop();
    });
    await flush();
    expect(h.cancelRun).toHaveBeenCalledTimes(1);
    expect(toastError).not.toHaveBeenCalled();
  });
});

describe("上传错误的提示（OCR 第 1 轮）", () => {
  test("上传停滞错误映射为用户可读文案，而不是英文原串", async () => {
    await mount("t-1");
    const stalled = Object.assign(
      new Error("Upload stalled: no network progress for 120s"),
      { name: "UploadStalledError" },
    );
    h.upload.mockRejectedValueOnce(stalled);

    await expect(
      api().sendMessage("t-1", { text: "A", files: [ATTACHMENT] }),
    ).rejects.toMatchObject({ kind: "failed" });

    expect(toastError).toHaveBeenCalledTimes(1);
    const first = toastError.mock.calls[0]?.[0];
    expect(typeof first === "string" ? first : "").toContain("没有网络进展");
    expect(typeof first === "string" ? first : "").not.toMatch(
      /Upload stalled/,
    );
  });
});
