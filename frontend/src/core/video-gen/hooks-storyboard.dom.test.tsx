/**
 * E1 占位响应回归（2026-08-20）：草稿生成期后端返 200 {status:"generating"}，
 * 修前裸对象入缓存被当 StoryboardData 分发，detail 页 storyboard.frames.length
 * 抛 TypeError 整页崩溃。修复：组件层 select 归一化为 undefined，raw cache 保留
 * 占位信号供 storyboardRefetchInterval 2s 轮询。模式沿用 hooks-add-frame.test.tsx。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetch } from "@/core/api/fetcher";
import {
  STORYBOARD_KEY,
  storyboardRefetchInterval,
  useStoryboard,
  useStoryboardWithAudioPolling,
} from "@/core/video-gen/hooks";
import type { StoryboardData } from "@/core/video-gen/types";

vi.mock("@/core/api/fetcher", () => ({ fetch: vi.fn() }));
vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockFetch = vi.mocked(fetch);

const GENERATING = { status: "generating" };
const REAL_STORYBOARD = {
  title: "t",
  config: {},
  frames: [{ index: 0, narration: "n" }],
  total_duration: 6,
  status: "draft",
};

function asRes(obj: Record<string, unknown>): Response {
  return obj as unknown as Response;
}

function mockStoryboard(body: Record<string, unknown>) {
  mockFetch.mockResolvedValueOnce(
    asRes({ ok: true, status: 200, json: () => Promise.resolve(body) }),
  );
}

let latestPolling: ReturnType<typeof useStoryboardWithAudioPolling> | null =
  null;
let latestPlain: ReturnType<typeof useStoryboard> | null = null;

function PollingProbe({ taskId }: { taskId: string }) {
  latestPolling = useStoryboardWithAudioPolling(taskId, false);
  return null;
}
function PlainProbe({ taskId }: { taskId: string }) {
  latestPlain = useStoryboard(taskId);
  return null;
}

describe("E1 分镜占位响应 {status:generating}", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    latestPolling = null;
    latestPlain = null;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  async function renderAndSettle(probe: ReactElement) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>{probe}</QueryClientProvider>,
      );
    });
    // 让 queryFn promise 解析 + RQ 状态落盘
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  it("storyboardRefetchInterval：raw 占位数据仍返回 2000ms 轮询", () => {
    const query = {
      state: {
        data: GENERATING as unknown as StoryboardData,
        error: null,
      },
    };
    expect(storyboardRefetchInterval(query, false, false)).toBe(2000);
  });

  it("useStoryboardWithAudioPolling：占位响应组件层归一化为 undefined，raw cache 保留", async () => {
    mockStoryboard(GENERATING);
    await renderAndSettle(<PollingProbe taskId="vg_t1" />);

    expect(latestPolling!.isSuccess).toBe(true);
    expect(latestPolling!.data).toBeUndefined();
    // raw cache 保留占位 → refetchInterval 的 2s 分支仍能看到它
    expect(qc.getQueryData([STORYBOARD_KEY, "vg_t1"])).toEqual(GENERATING);
  });

  it("useStoryboardWithAudioPolling：真实 storyboard 原样透传", async () => {
    mockStoryboard(REAL_STORYBOARD);
    await renderAndSettle(<PollingProbe taskId="vg_t2" />);

    expect(latestPolling!.isSuccess).toBe(true);
    expect(latestPolling!.data?.frames).toHaveLength(1);
  });

  it("useStoryboard：占位响应组件层归一化为 undefined", async () => {
    mockStoryboard(GENERATING);
    await renderAndSettle(<PlainProbe taskId="vg_t3" />);

    expect(latestPlain!.isSuccess).toBe(true);
    expect(latestPlain!.data).toBeUndefined();
  });
});
