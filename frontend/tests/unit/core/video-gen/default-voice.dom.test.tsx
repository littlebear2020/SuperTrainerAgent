/**
 * Plan B Task 4（2026-10-19）：fetchDefaultVoice 的 taskType 参数。
 *
 * 契约：
 * - 无 taskType：URL 不带 task_type（旧调用方零变化）。
 * - taskType="vimax"：URL 追加 task_type=vimax（后端 ViMax 默认解析）。
 * - useDefaultVoice 的 query key 含 taskType（两参数不共用缓存）。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchDefaultVoice } from "@/core/video-gen/api";
import { useDefaultVoice } from "@/core/video-gen/hooks";

const urls: string[] = [];

function mockFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string | URL) => {
      urls.push(String(url));
      return {
        ok: true,
        json: async () => ({ voice_id: "doubao:zh_male_A" }),
      } as Response;
    }),
  );
}

beforeEach(() => {
  urls.length = 0;
  mockFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchDefaultVoice", () => {
  it("无 taskType：URL 不带 task_type", async () => {
    await fetchDefaultVoice("zh");
    expect(urls[0]).not.toContain("task_type");
    expect(urls[0]).toContain("lang=zh");
  });

  it("taskType=vimax：URL 追加 task_type=vimax", async () => {
    await fetchDefaultVoice("zh", "vimax");
    expect(urls[0]).toContain("task_type=vimax");
  });
});

describe("useDefaultVoice query key", () => {
  it("query key 区分 taskType（两参数不共用缓存）", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    let root: Root | null = createRoot(container);

    const keySpy = vi.fn();

    function Probe({ lang, taskType }: { lang: string; taskType?: string }) {
      const q = useDefaultVoice(lang, taskType);
      keySpy(q.dataUpdatedAt);
      return <pre data-testid="voice">{q.data ?? "loading"}</pre>;
    }

    const qc = new QueryClient();
    await act(async () => {
      root?.render(
        <QueryClientProvider client={qc}>
          <Probe lang="zh" taskType="vimax" />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain("doubao:zh_male_A");
      });
    });

    // 换 taskType → 新 key → 重新请求（URL 数量增加）
    const before = urls.length;
    await act(async () => {
      root?.render(
        <QueryClientProvider client={qc}>
          <Probe lang="zh" />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(urls.length).toBeGreaterThan(before);
      });
    });

    await act(async () => {
      root?.unmount();
    });
    root = null;
    container.remove();
  });
});
