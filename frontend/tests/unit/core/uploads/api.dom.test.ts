/**
 * PR2（2026-11-24 前端可靠性批）：uploadFilesWithProgress 的空闲超时与取消。
 *
 * 此前没有任何超时：网络半开时上传永远挂着、输入框一直处于上传态。现在上传与
 * 等待响应共用一个空闲计时（默认 120 秒无网络进展即失败），每次进展重置；
 * 预先 aborted 的 signal 不发 XHR。XMLHttpRequest 以桩替身驱动（happy-dom 的 XHR
 * 会真的发请求）。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  uploadFilesWithProgress,
  UPLOAD_IDLE_TIMEOUT_MS,
} from "@/core/uploads/api";

vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

type Listener = (event: {
  lengthComputable?: boolean;
  loaded: number;
  total: number;
}) => void;

/** 可控 XHR 桩：记录 open/send/abort，手动触发 upload/load/error/abort 事件。 */
function installXhrStub() {
  const instances: XhrStub[] = [];
  class XhrStub {
    upload = { listeners: new Map<string, Listener[]>() };
    withCredentials = false;
    status = 0;
    statusText = "";
    responseText = "";
    headers: Record<string, string> = {};
    sent: FormData | null = null;
    aborted = false;
    private listeners = new Map<string, Listener[]>();
    open = vi.fn();
    setRequestHeader(name: string, value: string) {
      this.headers[name] = value;
    }
    addEventListener(type: string, listener: Listener) {
      this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
    }
    send(body: FormData) {
      this.sent = body;
    }
    abort() {
      this.aborted = true;
      this.emit("abort", { loaded: 0, total: 0 });
    }
    emit(
      type: string,
      event: { lengthComputable?: boolean; loaded: number; total: number },
    ) {
      for (const listener of this.listeners.get(type) ?? []) listener(event);
    }
    emitUpload(loaded: number, total: number) {
      for (const listener of this.upload.listeners.get("progress") ?? []) {
        listener({ lengthComputable: true, loaded, total });
      }
    }
    constructor() {
      instances.push(this);
      Object.defineProperty(this.upload, "addEventListener", {
        configurable: true,
        value: (type: string, listener: Listener) => {
          this.upload.listeners.set(type, [
            ...(this.upload.listeners.get(type) ?? []),
            listener,
          ]);
        },
      });
    }
  }
  const target = globalThis as unknown as { XMLHttpRequest: unknown };
  const original = target.XMLHttpRequest;
  target.XMLHttpRequest = XhrStub;
  return {
    instances,
    restore() {
      target.XMLHttpRequest = original;
    },
  };
}

describe("uploadFilesWithProgress 空闲超时与取消", () => {
  let xhr: ReturnType<typeof installXhrStub>;

  beforeEach(() => {
    vi.useFakeTimers();
    xhr = installXhrStub();
    Object.defineProperty(document, "cookie", {
      configurable: true,
      value: "",
    });
  });

  afterEach(() => {
    xhr.restore();
    vi.useRealTimers();
  });

  test("无网络进展 120 秒 reject，并中止 XHR；之后的事件不再 settle", async () => {
    const promise = uploadFilesWithProgress("t1", [new File(["x"], "a.txt")]);
    const observed = promise.then(
      () => "resolved",
      (error: Error) => error,
    );
    expect(xhr.instances).toHaveLength(1);
    const instance = xhr.instances[0]!;

    await vi.advanceTimersByTimeAsync(UPLOAD_IDLE_TIMEOUT_MS - 1000);
    // 还没到点：未 settle
    expect(await Promise.race([observed, Promise.resolve("pending")])).toBe(
      "pending",
    );

    await vi.advanceTimersByTimeAsync(1000);
    const error = await observed;
    expect(error).toMatchObject({
      message: expect.stringMatching(/no network progress for 120s/),
    });
    expect(instance.aborted).toBe(true);

    // 迟到的 load 不会再次 settle（resolve 被忽略，不抛、不覆盖）
    instance.status = 200;
    instance.responseText = JSON.stringify({ files: [] });
    instance.emit("load", { loaded: 10, total: 10 });
    expect(await observed).toBe(error);
  });

  test("持续的网络进展重置空闲计时（上行 progress 与下行 progress 都算）", async () => {
    const onProgress = vi.fn();
    const promise = uploadFilesWithProgress(
      "t1",
      [new File(["x"], "a.txt")],
      onProgress,
    );
    const observed = promise.then(
      () => "resolved",
      (error: Error) => error,
    );
    const instance = xhr.instances[0]!;

    for (let round = 0; round < 3; round++) {
      await vi.advanceTimersByTimeAsync(UPLOAD_IDLE_TIMEOUT_MS - 1000);
      instance.emitUpload(10 * (round + 1), 100);
    }
    // 上传完、等响应：下行 progress 也重置
    instance.emit("progress", { loaded: 10, total: 0 });
    await vi.advanceTimersByTimeAsync(UPLOAD_IDLE_TIMEOUT_MS - 1000);
    expect(onProgress).toHaveBeenCalledTimes(3);
    expect(await Promise.race([observed, Promise.resolve("pending")])).toBe(
      "pending",
    );

    instance.status = 200;
    instance.responseText = JSON.stringify({
      success: true,
      files: [{ filename: "a.txt" }],
      message: "ok",
    });
    instance.emit("load", { loaded: 10, total: 10 });
    expect(await observed).toBe("resolved");
  });

  test("预先 aborted 的 signal：不发 XHR，立即以 AbortError reject", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      uploadFilesWithProgress("t1", [new File(["x"], "a.txt")], undefined, {
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(xhr.instances).toHaveLength(0);
  });

  test("外部 abort：中止 XHR 并以 AbortError reject，监听与计时器被清理", async () => {
    const controller = new AbortController();
    const observed = uploadFilesWithProgress(
      "t1",
      [new File(["x"], "a.txt")],
      undefined,
      { signal: controller.signal },
    ).then(
      () => "resolved",
      (error: Error) => error,
    );
    const instance = xhr.instances[0]!;
    const removeSpy = vi.spyOn(controller.signal, "removeEventListener");

    controller.abort();
    await expect(observed).resolves.toMatchObject({ name: "AbortError" });
    expect(instance.aborted).toBe(true);
    // 空闲计时器已清理：再过很久也不会有第二次 settle / 未处理拒绝
    await vi.advanceTimersByTimeAsync(UPLOAD_IDLE_TIMEOUT_MS * 2);
    expect(removeSpy).toHaveBeenCalledWith("abort", expect.any(Function));
  });

  test("load / error 之后清理空闲计时器（不会在成功后又超时 reject）", async () => {
    const observed = uploadFilesWithProgress(
      "t1",
      [new File(["x"], "a.txt")],
      undefined,
      { idleTimeoutMs: 5_000 },
    ).then(
      () => "resolved",
      (error: Error) => error,
    );
    const instance = xhr.instances[0]!;
    instance.status = 500;
    instance.statusText = "Server Error";
    instance.emit("load", { loaded: 0, total: 0 });
    await expect(observed).resolves.toMatchObject({
      message: "500: Server Error",
    });

    await vi.advanceTimersByTimeAsync(60_000);
    expect(instance.aborted).toBe(false);
  });
});
