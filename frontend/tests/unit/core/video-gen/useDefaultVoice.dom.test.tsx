/**
 * Task 15（FP1-5）: useDefaultVoice 返回后端解析的默认音色
 * （doubao:<speaker> 或 preset:146），供「默认」选项试听——
 * 修前试听恒用 types.ts LANG_OPTIONS 的静态 edge-tts 值，与成品声音不一致。
 *
 * 注：仓库未装 @testing-library/react（见 useSubmitVideo-error.test.tsx 头注的
 * 既定约定），这里复用 Task 11 的 react-dom/client createRoot + react act 手写
 * 最小 renderHook；happy-dom 提供 DOM。路径用 @/ 别名（tests/unit 镜像 src 布局）。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetch } from "@/core/api/fetcher";
import { useDefaultVoice } from "@/core/video-gen/hooks";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/core/api/fetcher", () => ({ fetch: vi.fn() }));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockFetch = vi.mocked(fetch);

/** Cast partial mock response to Response for vitest mocks. */
function asRes(obj: Record<string, unknown>): Response {
  return obj as unknown as Response;
}

type HookResult = ReturnType<typeof useDefaultVoice>;

let latest: HookResult | null = null;

function Probe({ lang }: { lang: string }) {
  latest = useDefaultVoice(lang);
  return null;
}

describe("useDefaultVoice（FP1-5：默认音色试听对齐成品）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    latest = null;
    mockFetch.mockResolvedValue(
      asRes({
        ok: true,
        json: () => Promise.resolve({ voice_id: "preset:146" }),
      }),
    );
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  async function renderProbe() {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Probe lang="zh" />
        </QueryClientProvider>,
      );
    });
    // 排空 queryFn 异步链（fetch → json → React Query state 更新）
    for (let i = 0; i < 50 && latest?.data === undefined; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 10));
      });
    }
  }

  it("返回后端解析的默认音色", async () => {
    await renderProbe();

    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8001/api/video/voices/default?lang=zh",
      { credentials: "include" },
    );
    expect(latest?.data).toBe("preset:146");
  });

  it("接口失败时 data 为空（page 兜底链退化到静态值而非错声）", async () => {
    mockFetch.mockResolvedValue(
      asRes({ ok: false, status: 503, json: () => Promise.resolve({}) }),
    );
    await renderProbe();

    expect(latest?.data).toBeUndefined();
    expect(latest?.isError).toBe(true);
  });
});
