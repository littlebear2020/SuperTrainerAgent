/**
 * FP1-8 / P2-21: 角色三视图 generate/upload 与项目 CRUD 失败必须发声。
 * 修前：
 * - useGenerateCharacterSheet/useUploadCharacterSheet 抛泛化文案（吞服务端 detail）且无 onError toast；
 * - useCreateCourseProject/useUpdateCourseProject/useDeleteCourseProject 无 onError，失败全静默。
 *
 * 注：仓库未装 @testing-library/react（沿用 useSubmitVideo-error.test.tsx 的
 * createRoot + act 手写 renderHook 约定）；happy-dom 提供 DOM。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetch as csrfFetch } from "@/core/api/fetcher";
import {
  createCourseProject,
  deleteCourseProject,
  updateCourseProject,
} from "@/core/video-gen/api";
import {
  useCreateCourseProject,
  useDeleteCourseProject,
  useGenerateCharacterSheet,
  useUpdateCourseProject,
  useUploadCharacterSheet,
} from "@/core/video-gen/hooks";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));
vi.mock("@/core/api/fetcher", () => ({ fetch: vi.fn() }));
vi.mock("@/core/video-gen/api", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    createCourseProject: vi.fn(),
    updateCourseProject: vi.fn(),
    deleteCourseProject: vi.fn(),
  };
});

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockFetch = vi.mocked(csrfFetch);
const mockCreate = vi.mocked(createCourseProject);
const mockUpdate = vi.mocked(updateCourseProject);
const mockDelete = vi.mocked(deleteCourseProject);

/** 500 JSON 响应（hooks 读 res.json() 取 detail）。 */
function errorResponse(detail: string) {
  return {
    ok: false,
    status: 500,
    json: () => Promise.resolve({ detail }),
  } as unknown as Response;
}

let cleanups: Array<() => Promise<void>> = [];

async function renderHook<T>(useHook: () => T): Promise<() => T> {
  let latest: T | null = null;
  function Probe() {
    latest = useHook();
    return null;
  }
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root: Root = createRoot(container);
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
  cleanups.push(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
  return () => {
    if (latest === null) throw new Error("probe not rendered");
    return latest;
  };
}

/** 触发 mutation 并排空 rejected mutationFn 的微任务链（→ onError → state 更新）。 */
async function runMutation(fn: () => void) {
  await act(async () => {
    fn();
    await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  cleanups = [];
});

afterEach(async () => {
  for (const cleanup of cleanups) await cleanup();
});

describe("useGenerateCharacterSheet（FP1-8：失败发声）", () => {
  it("失败时 toast 与 error 均含服务端 detail（修前泛化文案且无 toast）", async () => {
    mockFetch.mockResolvedValue(errorResponse("生图服务不可用"));
    const current = await renderHook(() => useGenerateCharacterSheet("p1"));

    await runMutation(() =>
      current().mutate({ name: "n", description: "d", features: "f" }),
    );

    expect(current().isError).toBe(true);
    expect(current().error?.message).toContain("生图服务不可用");
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("生图服务不可用"),
    );
  });

  it("非 JSON 错误响应回退默认文案且仍 toast", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 502,
      json: () => Promise.reject(new Error("bad gateway html")),
    } as unknown as Response);
    const current = await renderHook(() => useGenerateCharacterSheet("p1"));

    await runMutation(() =>
      current().mutate({ name: "n", description: "d", features: "f" }),
    );

    expect(current().isError).toBe(true);
    expect(toast.error).toHaveBeenCalledWith("生成角色三视图失败");
  });
});

describe("useUploadCharacterSheet（FP1-8：失败发声）", () => {
  it("失败时 toast 与 error 均含服务端 detail（修前泛化文案且无 toast）", async () => {
    mockFetch.mockResolvedValue(errorResponse("图片未通过内容审查"));
    const current = await renderHook(() => useUploadCharacterSheet("p1"));

    await runMutation(() => current().mutate(new FormData()));

    expect(current().isError).toBe(true);
    expect(current().error?.message).toContain("图片未通过内容审查");
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("图片未通过内容审查"),
    );
  });
});

describe("项目 CRUD（P2-21：失败发声）", () => {
  it("useCreateCourseProject 失败 toast 透传错误文案（修前静默）", async () => {
    mockCreate.mockRejectedValue(new Error("HTTP 500: db down"));
    const current = await renderHook(() => useCreateCourseProject());

    await runMutation(() => current().mutate({ title: "t" }));

    expect(current().isError).toBe(true);
    expect(toast.error).toHaveBeenCalledWith("HTTP 500: db down");
  });

  it("useUpdateCourseProject 失败 toast 透传错误文案（修前静默）", async () => {
    mockUpdate.mockRejectedValue(new Error("HTTP 409: conflict"));
    const current = await renderHook(() => useUpdateCourseProject());

    await runMutation(() => current().mutate({ id: "p1", input: {} }));

    expect(current().isError).toBe(true);
    expect(toast.error).toHaveBeenCalledWith("HTTP 409: conflict");
  });

  it("useDeleteCourseProject 失败 toast 透传错误文案（修前静默且调用方仍跳走）", async () => {
    mockDelete.mockRejectedValue(new Error("HTTP 403: forbidden"));
    const current = await renderHook(() => useDeleteCourseProject());

    await runMutation(() => current().mutate("p1"));

    expect(current().isError).toBe(true);
    expect(toast.error).toHaveBeenCalledWith("HTTP 403: forbidden");
  });
});
