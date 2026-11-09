/**
 * assets-api CSRF attachment tests.
 *
 * Bug: `generateImage` used bare `globalThis.fetch`, bypassing the fetcher
 * wrapper that mints `X-CSRF-Token` from the `csrf_token` cookie →
 * `POST /api/writer/assets/images/generate` was rejected with 403
 * "CSRF token missing". These tests pin the contract that the wrapper is used:
 * when the csrf cookie is present, the outbound POST carries `X-CSRF-Token`
 * (and credentials).
 *
 * Runs in Node (no DOM), so `document` is stubbed to provide the cookie. The
 * REAL fetcher wrapper is exercised (not mocked) so the cookie→header path is
 * genuine — this is the regression guard for the CSRF bypass.
 */

import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import * as assetsApi from "@/core/writer/assets-api";
import {
  GenerateImageError,
  generateImage,
  parseGenerateImageError,
  uploadImage,
} from "@/core/writer/assets-api";

let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.stubGlobal("document", { cookie: "csrf_token=test-token-abc" });
  fetchSpy = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(
      new Response(
        JSON.stringify({ url: "/x.png", width: 1, height: 1, source: "s" }),
        { status: 200 },
      ),
    );
});

afterEach(() => {
  fetchSpy.mockRestore();
  vi.unstubAllGlobals();
});

test("generateImage POST carries X-CSRF-Token minted from the csrf cookie", async () => {
  await generateImage({ prompt: "a cat", size: "1024x1024" });

  expect(fetchSpy).toHaveBeenCalledTimes(1);
  const [, init] = fetchSpy.mock.calls[0]!;
  const options = init as RequestInit;
  expect(options.method).toBe("POST");
  const headers = new Headers(options.headers);
  expect(headers.get("X-CSRF-Token")).toBe("test-token-abc");
  // Wrapper forces credentials so the access_token cookie travels.
  expect(options.credentials).toBe("include");
});

test("generateImage posts to the writer assets generate endpoint", async () => {
  await generateImage({ prompt: "x" });
  const [url] = fetchSpy.mock.calls[0]!;
  expect(url).toBe("/api/writer/assets/images/generate");
});

test("generateImage sends prompt + default size in the JSON body", async () => {
  await generateImage({ prompt: "a dog" });
  const options = fetchSpy.mock.calls[0]![1] as RequestInit;
  expect(JSON.parse(options.body as string)).toEqual({
    prompt: "a dog",
    size: "1024x1024",
  });
});

// ── uploadImage (paste / drop — issue ①) ──────────────────────────────────

test("uploadImage POSTs the file as multipart with the CSRF header", async () => {
  fetchSpy.mockResolvedValue(
    new Response(
      JSON.stringify({
        url: "/api/writer/assets/images/raw/abc.png",
        width: null,
        height: null,
        source: "upload",
      }),
      { status: 200 },
    ),
  );
  const file = new File(["data"], "x.png", { type: "image/png" });

  const result = await uploadImage(file);

  const [url, init] = fetchSpy.mock.calls[0]!;
  expect(url).toBe("/api/writer/assets/images/upload");
  const options = init as RequestInit;
  expect(options.method).toBe("POST");
  expect(new Headers(options.headers).get("X-CSRF-Token")).toBe(
    "test-token-abc",
  );
  // Body is multipart FormData carrying the file (no manual Content-Type — the
  // browser sets the boundary).
  expect(options.body).toBeInstanceOf(FormData);
  expect((options.body as FormData).get("file")).toBe(file);
  expect(result.url).toBe("/api/writer/assets/images/raw/abc.png");
  expect(result.source).toBe("upload");
});

test("uploadImage throws backend detail on failure", async () => {
  fetchSpy.mockResolvedValue(
    new Response(JSON.stringify({ detail: "Unsupported image type" }), {
      status: 400,
    }),
  );
  const file = new File(["data"], "evil.svg", { type: "image/svg+xml" });
  await expect(uploadImage(file)).rejects.toThrow("Unsupported image type");
});

test("generateImage throws GenerateImageError with structured detail on 503", async () => {
  fetchSpy.mockResolvedValue(
    new Response(
      JSON.stringify({
        detail: {
          message: "AI 生图未配置：缺少图像模型 API 密钥",
          action: "请联系管理员在配置中设置 qihu_api_key 后重启服务",
          reason: "qihu_key_missing",
        },
      }),
      { status: 503 },
    ),
  );
  try {
    await generateImage({ prompt: "x" });
    expect.fail("should have thrown");
  } catch (e) {
    expect(e).toBeInstanceOf(GenerateImageError);
    const err = e as GenerateImageError;
    expect(err.message).toBe("AI 生图未配置：缺少图像模型 API 密钥");
    expect(err.action).toBe("请联系管理员在配置中设置 qihu_api_key 后重启服务");
    expect(err.reason).toBe("qihu_key_missing");
  }
});

test("generateImage throws GenerateImageError with plain string detail (rate limit)", async () => {
  fetchSpy.mockResolvedValue(
    new Response(
      JSON.stringify({
        detail: "AI 生图请求过于频繁（每分钟 5 次），请稍后再试",
      }),
      { status: 429 },
    ),
  );
  try {
    await generateImage({ prompt: "x" });
    expect.fail("should have thrown");
  } catch (e) {
    expect(e).toBeInstanceOf(GenerateImageError);
    const err = e as GenerateImageError;
    expect(err.message).toBe("AI 生图请求过于频繁（每分钟 5 次），请稍后再试");
    expect(err.action).toBeUndefined();
    expect(err.reason).toBeUndefined();
  }
});

// ── parseGenerateImageError (W19) ─────────────────────────────────────────

test("parseGenerateImageError extracts structured info from GenerateImageError", () => {
  const err = new GenerateImageError(
    "AI 生图未配置",
    "请联系管理员",
    "video_gen_unconfigured",
  );
  const info = parseGenerateImageError(err);
  expect(info.message).toBe("AI 生图未配置");
  expect(info.action).toBe("请联系管理员");
  expect(info.reason).toBe("video_gen_unconfigured");
});

test("parseGenerateImageError falls back to Error.message", () => {
  const info = parseGenerateImageError(new Error("some failure"));
  expect(info.message).toBe("some failure");
  expect(info.action).toBeUndefined();
  expect(info.reason).toBeUndefined();
});

test("parseGenerateImageError falls back to String for non-Error", () => {
  const info = parseGenerateImageError("weird value");
  expect(info.message).toBe("weird value");
  expect(info.action).toBeUndefined();
});

test("parseGenerateImageError handles null gracefully", () => {
  const info = parseGenerateImageError(null);
  expect(info.message).toBe("生成失败，请重试");
  expect(info.action).toBeUndefined();
});

// ── Pexels search removal regression guard ───────────────────────────────

test("searchImages is no longer exported from assets-api", () => {
  expect(assetsApi).not.toHaveProperty("searchImages");
  expect(assetsApi).not.toHaveProperty("ImageSearchResult");
});
