/**
 * P2-4 / BUG-007 / DSN-012：失败任务重试合成的错误处理收敛。
 *
 * 修前的结构缺陷：readErrorDetail 只回传 message string、丢弃 HTTP status，
 * 组件只能靠文案匹配判定 409——路由透传具体文案后该匹配更脆弱。
 *
 * 契约：
 * - VideoApiError 携带 status（对齐 writer 模块 WriterDocConflictError 模式）；
 * - recomposeTask/reRenderTask/addFrame/deleteFrame 非 ok 时抛 VideoApiError；
 * - useRecomposeTask 是唯一 toast 出口：409 → 单条 toast（duration 8000 + 「去编辑分镜」
 *   action，经回调参数注入导航）；其他错误 → 通用 toast；action 点击触发回调。
 *
 * 仓库无 @testing-library/react：createRoot + act + happy-dom 手写 harness。
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  fetchMock: vi.fn(),
  toastError: vi.fn(),
  onEditStoryboard: vi.fn(),
}));

vi.mock("@/core/api/fetcher", () => ({
  fetch: (...args: unknown[]) => h.fetchMock(...args),
}));

vi.mock("sonner", () => ({
  toast: { error: h.toastError, success: vi.fn() },
}));

import {
  VideoApiError,
  addFrame,
  deleteFrame,
  recomposeTask,
  reRenderTask,
} from "@/core/video-gen/api";
import { useRecomposeTask } from "@/core/video-gen/hooks";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function jsonResponse(status: number, detail: string) {
  return {
    ok: false,
    status,
    json: async () => ({ detail }),
  };
}

describe("VideoApiError（P2-4/BUG-007）", () => {
  it("携带 status 且 instanceof Error", () => {
    const e = new VideoApiError("缺音频", 409);
    expect(e).toBeInstanceOf(Error);
    expect(e).toBeInstanceOf(VideoApiError);
    expect(e.status).toBe(409);
    expect(e.message).toBe("缺音频");
  });

  it("recomposeTask 409 → 抛 VideoApiError（status + 后端 detail）", async () => {
    const msg =
      "缺音频（audio.mp3），无法重新合成，请先重新生成音频或整任务重生成";
    h.fetchMock.mockResolvedValueOnce(jsonResponse(409, msg));
    await expect(recomposeTask("vg_1")).rejects.toMatchObject({
      status: 409,
      message: msg,
    });
  });

  it("reRenderTask / addFrame / deleteFrame 非 ok 同样抛 VideoApiError", async () => {
    h.fetchMock.mockResolvedValueOnce(jsonResponse(409, "x1"));
    await expect(reRenderTask("vg_1")).rejects.toBeInstanceOf(VideoApiError);
    h.fetchMock.mockResolvedValueOnce(jsonResponse(409, "x2"));
    await expect(addFrame("vg_1", {})).rejects.toBeInstanceOf(VideoApiError);
    h.fetchMock.mockResolvedValueOnce(jsonResponse(400, "x3"));
    await expect(deleteFrame("vg_1", 0)).rejects.toMatchObject({
      status: 400,
    });
  });
});

describe("useRecomposeTask 唯一 toast 出口（P2-4）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.toastError.mockClear();
    h.onEditStoryboard.mockClear();
    h.fetchMock.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.body.innerHTML = "";
  });

  it("409 → 单条 toast：前缀+具体原因，duration 8000，action 点击回调导航", async () => {
    h.fetchMock.mockResolvedValueOnce(
      jsonResponse(409, "缺音频（audio.mp3），无法重新合成"),
    );
    let mutate: ((v?: undefined) => void) | undefined;
    function Probe() {
      const m = useRecomposeTask("vg_1", {
        onEditStoryboard: h.onEditStoryboard,
      });
      mutate = m.mutate;
      return null;
    }
    const qc = new QueryClient();
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Probe />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      mutate!(undefined);
    });
    // 单一出口：恰一次 toast.error
    expect(h.toastError).toHaveBeenCalledTimes(1);
    const [msg, opts] = h.toastError.mock.calls[0]!;
    expect(msg).toContain("重新渲染失败");
    expect(msg).toContain("缺音频（audio.mp3）");
    expect(opts?.duration).toBe(8000);
    // action 是可点按钮，点击触发注入的导航回调
    const action = opts?.action as React.ReactElement<{
      onClick: () => void;
      children?: React.ReactNode;
    }>;
    expect(action).toBeTruthy();
    const btn = document.createElement("div");
    // 渲染 action 节点到 DOM 再点击（sonner 的 action 就是 ReactNode）
    const { createRoot: cr } = await import("react-dom/client");
    const sub = cr(btn);
    await act(async () => {
      sub.render(action);
    });
    await act(async () => {
      (btn.querySelector("button") ?? btn).dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(h.onEditStoryboard).toHaveBeenCalledTimes(1);
  });

  it("非 VideoApiError（网络层）→ 通用 toast，无 409 分支", async () => {
    h.fetchMock.mockRejectedValueOnce(new Error("Network down"));
    let mutate: ((v?: undefined) => void) | undefined;
    function Probe() {
      const m = useRecomposeTask("vg_1");
      mutate = m.mutate;
      return null;
    }
    const qc = new QueryClient();
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Probe />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      mutate!(undefined);
    });
    expect(h.toastError).toHaveBeenCalledTimes(1);
    expect(h.toastError.mock.calls[0]![0]).toContain("Network down");
  });
});

describe("静态守卫（P2-4 防回退）", () => {
  const hooksSrc = readFileSync(
    join(process.cwd(), "src/core/video-gen/hooks.ts"),
    "utf-8",
  );
  const detailSrc = readFileSync(
    join(
      process.cwd(),
      "src/components/workspace/video-gen/video-gen-detail.tsx",
    ),
    "utf-8",
  );

  it("useRecomposeTask / useReRenderTask onError 走 409 分支（toastAction409）", () => {
    // 409 判断收敛在共享 helper toastAction409（两 hook 的 onError 均经它）
    expect(hooksSrc).toContain("err instanceof VideoApiError");
    expect(hooksSrc).toContain("err.status === 409");
    // 锚点校验：注释被改/删时 indexOf=-1，slice 退化成近全文件窗口、守卫静默失效
    const recomposeStart = hooksSrc.indexOf("export function useRecomposeTask");
    const recomposeEnd = hooksSrc.indexOf("// Task 20c");
    expect(recomposeStart).toBeGreaterThan(-1);
    expect(recomposeEnd).toBeGreaterThan(-1);
    const recompose = hooksSrc.slice(recomposeStart, recomposeEnd);
    expect(recompose).toContain("toastAction409(err, ");
    const reRenderStart = hooksSrc.indexOf("export function useReRenderTask");
    const reRenderEnd = hooksSrc.indexOf("// ── Course Projects");
    expect(reRenderStart).toBeGreaterThan(-1);
    expect(reRenderEnd).toBeGreaterThan(-1);
    const reRender = hooksSrc.slice(reRenderStart, reRenderEnd);
    expect(reRender).toContain("toastAction409(err, ");
  });

  it("failed 态按钮文案改为「渲染中断可断点续跑」+ 静态引导句在场", () => {
    expect(detailSrc).toContain("重试合成（渲染中断可断点续跑）");
    expect(detailSrc).not.toContain("重试合成（复用已生成画面）");
    expect(detailSrc).toContain("若重试提示缺素材/缺音频");
  });
});

describe("静态守卫（P3-3/BUG-006 前缀去重）", () => {
  const hooksSrc = readFileSync(
    join(process.cwd(), "src/core/video-gen/hooks.ts"),
    "utf-8",
  );

  it("useAddFrame/useDeleteFrame onError 不再拼「添加/删除分镜失败：」前缀", () => {
    expect(hooksSrc).not.toContain("添加分镜失败：");
    expect(hooksSrc).not.toContain("删除分镜失败：");
    const addStart = hooksSrc.indexOf("export function useAddFrame");
    const addEnd = hooksSrc.indexOf("/** D3");
    expect(addStart).toBeGreaterThan(-1);
    expect(addEnd).toBeGreaterThan(-1);
    const add = hooksSrc.slice(addStart, addEnd);
    expect(add).toContain("_frameEditErrorMessage");
    const delStart = hooksSrc.indexOf("export function useDeleteFrame");
    expect(delStart).toBeGreaterThan(-1);
    const delNext = hooksSrc.indexOf("\nexport function", delStart + 1);
    const del = hooksSrc.slice(delStart, delNext === -1 ? undefined : delNext);
    expect(del).toContain("_frameEditErrorMessage");
  });
});
