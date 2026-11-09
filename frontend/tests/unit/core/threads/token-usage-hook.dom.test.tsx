/**
 * D3 修订（2026-09-23 review #4）：useThreadTokenUsage 的零轮询契约。
 * 断言拉取只发生在「挂载 / refreshKey 递增（run 结束）」，时间推进不产生
 * 任何额外请求（守 DEF-06 零损耗红线）；enabled=false / threadId 未知 →
 * 不拉取、回 null。
 *
 * 注：仓库未装 @testing-library/react（见 useSubmitVideo-error.test.tsx 头注
 * 的既定约定），用 react-dom/client createRoot + react act 手写最小 renderHook；
 * happy-dom 提供 DOM。
 */

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useThreadTokenUsage } from "@/core/threads/token-usage";

const fetchMock = vi.hoisted(() => vi.fn());

vi.mock("@/core/api/fetcher", () => ({ fetch: fetchMock }));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

let container: HTMLElement | null = null;
let root: Root | null = null;
let bumpRefresh: (() => void) | null = null;

function Harness({
  threadId,
  enabled,
}: {
  threadId?: string;
  enabled: boolean;
}) {
  const [refreshKey, setRefreshKey] = useState(0);
  bumpRefresh = () => setRefreshKey((k) => k + 1);
  const usage = useThreadTokenUsage(threadId, enabled, refreshKey);
  return <div data-testid="usage">{usage ? usage.totalTokens : "null"}</div>;
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => ({
    ok: true,
    json: async () => ({
      thread_id: "t-1",
      total_tokens: 15_000_000,
      total_input_tokens: 10_000_000,
      total_output_tokens: 5_000_000,
    }),
  }));
});

afterEach(() => {
  if (root) {
    act(() => root!.unmount());
  }
  container = null;
  root = null;
  bumpRefresh = null;
});

function mount(props: { threadId?: string; enabled: boolean }) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  root.render(<Harness {...props} />);
  return container;
}

describe("useThreadTokenUsage", () => {
  it("fetches once on mount and never polls", async () => {
    vi.useFakeTimers();
    try {
      await act(async () => {
        mount({ threadId: "t-1", enabled: true });
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(
        container!.querySelector('[data-testid="usage"]')!.textContent,
      ).toBe("15000000");

      // 时间推进（远超任何合理轮询间隔）不得产生新请求
      await act(async () => {
        vi.advanceTimersByTime(30_000);
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refetches exactly once per refreshKey bump (run finished)", async () => {
    await act(async () => {
      mount({ threadId: "t-1", enabled: true });
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      bumpRefresh!();
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not fetch when thread is unknown", async () => {
    await act(async () => {
      mount({ threadId: undefined, enabled: true });
    });
    await act(async () => {
      bumpRefresh!();
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(container!.querySelector('[data-testid="usage"]')!.textContent).toBe(
      "null",
    );
  });
});
