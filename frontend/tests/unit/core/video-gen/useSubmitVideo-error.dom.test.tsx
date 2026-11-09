/**
 * FP1-3: useSubmitVideo 创建失败必须 toast 发声。
 * 修前 hooks.ts 只有 mutationFn + onSuccess、无 onError——
 * 403/422/500/网络错误全静默，用户分不清没点上/参数错/服务器挂
 * （FP0-1 的 remotion 保存草稿确定性 422 就是被这沉默放大的）。
 *
 * 注：仓库未装 @testing-library/react（见 tests/unit/core/voice-twin/hooks.test.ts
 * 头注的既定约定），这里用 react-dom/client createRoot + react act 手写最小
 * renderHook；happy-dom 提供 DOM。路径用 @/ 别名（tests/unit 镜像 src 布局，
 * 相对 ../hooks 指不到 src）。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { submitVideo } from "@/core/video-gen/api";
import { useSubmitVideo } from "@/core/video-gen/hooks";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/core/video-gen/api", () => ({
  submitVideo: vi.fn().mockRejectedValue(new Error("HTTP 422: video_type")),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));
vi.mock("@/core/api/fetcher", () => ({ fetch: vi.fn() }));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockSubmitVideo = vi.mocked(submitVideo);

type SubmitResult = ReturnType<typeof useSubmitVideo>;

let latest: SubmitResult | null = null;

function Probe() {
  latest = useSubmitVideo();
  return null;
}

function current(): SubmitResult {
  if (!latest) throw new Error("probe not rendered");
  return latest;
}

describe("useSubmitVideo（FP1-3：创建失败发声）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    latest = null;
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
      defaultOptions: { mutations: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Probe />
        </QueryClientProvider>,
      );
    });
  }

  it("创建失败弹 toast（修前静默）", async () => {
    await renderProbe();

    await act(async () => {
      current().mutate({} as never);
      // 等 rejected mutationFn 的微任务链（→ onError → state 更新）排空
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(current().isError).toBe(true);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("422"));
  });

  it("非 Error 拒绝（网络层裸字符串）弹兜底文案", async () => {
    mockSubmitVideo.mockRejectedValueOnce("network down");
    await renderProbe();

    await act(async () => {
      current().mutate({} as never);
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(current().isError).toBe(true);
    expect(toast.error).toHaveBeenCalledWith("创建视频任务失败，请重试");
  });
});
