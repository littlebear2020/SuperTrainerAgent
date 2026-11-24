import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { promptInputFilePartToFile } from "@/core/uploads/prompt-input-files";

/**
 * PR2（2026-11-24 前端可靠性批）：附件 URL fallback 转换接收取消信号。中止不是
 * 「获取失败」：不刷误导性 warn、不返回 null（= 静默丢附件），而是把 AbortError 原样抛出。
 */
describe("promptInputFilePartToFile 的取消信号", () => {
  let warn: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("预 aborted 的 signal：不发 fetch，直接以 AbortError reject", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(
      promptInputFilePartToFile(
        {
          type: "file",
          url: "blob:abc",
          filename: "a.txt",
          mediaType: "text/plain",
        },
        { signal: controller.signal },
      ),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
  });

  test("中途 abort：fetch 被中止，同样以 AbortError reject 且不 warn", async () => {
    const controller = new AbortController();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      (_input, init) =>
        new Promise<Response>((_, reject) => {
          init?.signal?.addEventListener("abort", () => {
            const error = new Error("The operation was aborted");
            error.name = "AbortError";
            reject(error);
          });
        }),
    );

    const pending = promptInputFilePartToFile(
      {
        type: "file",
        url: "blob:abc",
        filename: "a.txt",
        mediaType: "text/plain",
      },
      { signal: controller.signal },
    );
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    // 中途 abort：请求已经发出，是被信号打断的
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(warn).not.toHaveBeenCalled();
  });

  test("真实获取失败：仍返回 null 并 warn（既有语义不变）", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
    } as Response);
    await expect(
      promptInputFilePartToFile(
        {
          type: "file",
          url: "blob:missing",
          filename: "a.txt",
          mediaType: "text/plain",
        },
        { signal: new AbortController().signal },
      ),
    ).resolves.toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
  });
});
