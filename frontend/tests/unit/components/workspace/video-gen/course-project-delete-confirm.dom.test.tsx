/**
 * Task 20（前端 B12）：删除项目必须二次确认。
 *
 * 修复前 `course-project-dialog.tsx:112`：
 *   onClick={() => deleteMut.mutate(p.id)}
 * 一个 7×7px 的垃圾桶图标，**一点即删**，无任何确认。项目一删就没了，
 * 关联视频任务会失去项目关联（不可撤销）。
 *
 * 同仓 `course-project-detail.tsx` 的同一操作早就有 AlertDialog —— 本任务
 * 就是把那套模式复用过来。
 *
 * 本测试锁两条不变量：点垃圾桶只开确认框、**不发请求**；点确认后才真正删。
 *
 * 仓库无 @testing-library/react：沿用同目录 draft-parity.test.tsx 的
 * createRoot + act + happy-dom 手写 harness。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  deleteMutate: vi.fn(),
  projects: [] as unknown[],
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useCourseProjects: () => ({ data: h.projects, isLoading: false }),
  useDeleteCourseProject: () => ({
    mutate: h.deleteMutate,
    isPending: false,
  }),
  useCreateCourseProject: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  PROJECT_KEY: "course-projects",
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("Task 20: 删除项目二次确认（前端 B12）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.deleteMutate.mockClear();
    h.projects = [
      { id: "cp_aaaaaaaaaaaa", title: "要删的项目", video_count: 3 },
    ];
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

  async function renderDialog() {
    const { CourseProjectDialog: Dialog } =
      await import("@/components/workspace/video-gen/course-project-dialog");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Dialog onSelect={vi.fn()} />
        </QueryClientProvider>,
      );
    });
    // open 是组件内部 state（无 open prop）——必须点 DialogTrigger 才渲染项目列表
    const trigger = Array.from(container.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").includes("课程项目"),
    );
    if (!trigger) throw new Error("找不到打开对话框的触发按钮");
    await act(async () => {
      trigger.click();
    });
  }

  /** 垃圾桶按钮：无文字，用 lucide 的 svg class 定位。 */
  function trashButton(): HTMLButtonElement {
    const btns = Array.from(document.querySelectorAll("button"));
    const found = btns.find((b) => b.querySelector("svg.lucide-trash-2"));
    if (!found) throw new Error("找不到删除（垃圾桶）按钮");
    return found;
  }

  it("点垃圾桶只弹确认框，不得直接发删除请求", async () => {
    await renderDialog();

    await act(async () => {
      trashButton().click();
    });

    expect(
      h.deleteMutate,
      "一点即删是 B12 的核心缺陷：必须先确认",
    ).not.toHaveBeenCalled();
    expect(document.body.textContent ?? "").toMatch(/确认删除|无法撤销/);
  });

  it("确认框点「取消」后不删除", async () => {
    await renderDialog();

    await act(async () => {
      trashButton().click();
    });
    const cancel = Array.from(document.querySelectorAll("button")).find(
      (b) => (b.textContent ?? "").trim() === "取消",
    );
    expect(cancel, "确认框应有取消按钮").toBeTruthy();
    await act(async () => {
      cancel!.click();
    });

    expect(h.deleteMutate).not.toHaveBeenCalled();
  });

  it("确认框点确认后才真正删除，且带对的 id", async () => {
    await renderDialog();

    await act(async () => {
      trashButton().click();
    });
    const confirm = Array.from(document.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").trim().startsWith("删除"),
    );
    expect(confirm, "确认框应有确认删除按钮").toBeTruthy();
    await act(async () => {
      confirm!.click();
    });

    expect(h.deleteMutate).toHaveBeenCalledWith("cp_aaaaaaaaaaaa");
  });
});
