/**
 * Task 18（前端 B1）：未保存的项目编辑不得被 refetch 静默覆盖。
 *
 * 修复前：`course-project-detail.tsx` 的 `project` 由
 * `projectsQ.data?.find(p => p.id === projectId)` 每次 **重新 derive 出新对象**，
 * 而同步 state 的 useEffect 依赖 `[project]` —— 引用每次都变 → effect 每次
 * refetch 都重跑 → setTitle/setStylePreset/setCharacterReference/setPalette
 * 把用户正在输入的内容全部覆盖回服务端值。
 *
 * 四个角色图 mutation 都会 invalidateQueries([PROJECT_KEY])，所以这在真实
 * 使用中一触即发：改了标题还没保存，随手传一张角色图，标题就被打回去了。
 *
 * 本测试锁的不变量：**refetch 返回同 id 的新对象时，未保存的输入必须保留**；
 * 同时切换到另一个项目（id 变）时必须重新同步（否则会串项目数据）。
 *
 * 仓库无 @testing-library/react：沿用同目录 storyboard-no-auto-preview.test.tsx
 * 的 createRoot + act + happy-dom 手写 harness。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Project = {
  id: string;
  title: string;
  visual_style_preset?: string | null;
  character_reference?: string | null;
  color_palette?: Record<string, unknown> | null;
};

const h = vi.hoisted(() => ({
  projects: [] as unknown[],
  updateMutate: vi.fn(),
  deleteMutate: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

const stubMut = () => ({ mutate: vi.fn(), isPending: false });

vi.mock("@/core/video-gen/hooks", () => ({
  useCourseProjects: () => ({ data: h.projects, isLoading: false }),
  useUpdateCourseProject: () => ({ mutate: h.updateMutate, isPending: false }),
  useDeleteCourseProject: () => ({ mutate: h.deleteMutate, isPending: false }),
  useVideoTasks: () => ({ data: [], isLoading: false }),
  useUploadCharacterSheet: stubMut,
  useGenerateCharacterSheet: stubMut,
  useDeleteCharacterSheet: stubMut,
  useReorderCharacterSheets: stubMut,
  useVerifyCharacterSheet: stubMut,
  // C6/C7/M6：detail 页新增的锚图/音色/道具 hooks（mock 面对齐——断言不变）
  useGenerateStyleAnchor: stubMut,
  useSetStyleAnchor: stubMut,
  useDeleteStyleAnchor: stubMut,
  useClearStyleAnchorCandidate: stubMut,
  useUploadPropAsset: stubMut,
  useDeletePropAsset: stubMut,
  useVideoVoices: () => ({ data: [] }),
  useCourseProject: () => ({ data: undefined }),
  PROJECT_KEY: "course-projects",
}));

/**
 * 驱动受控输入：必须走原型上的 value setter。
 *
 * 直接 `el.value = x` 会被 React 的 value tracker 判定为「没变化」而**不触发
 * onChange** —— state 不动，只有 DOM 显示变了，断言就变成空的。
 */
function setNativeValue(
  el: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(proto, "value");
  desc?.set?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function makeProject(over: Partial<Project> = {}): Project {
  return {
    id: "cp_abc123def456",
    title: "原始标题",
    visual_style_preset: "flat",
    character_reference: "",
    color_palette: { primary: "#112233", saturation: 1.0, contrast: 1.0 },
    ...over,
  };
}

describe("Task 18: 未保存的项目编辑不被 refetch 覆盖（前端 B1）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.projects = [makeProject()];
    h.updateMutate.mockClear();
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

  async function renderDetail(projectId = "cp_abc123def456") {
    const { CourseProjectDetailPage: CourseProjectDetail } =
      await import("@/components/workspace/video-gen/course-project-detail");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <CourseProjectDetail projectId={projectId} onBack={vi.fn()} />
        </QueryClientProvider>,
      );
    });
    return { CourseProjectDetail, qc };
  }

  function titleInput(): HTMLInputElement {
    const inputs = Array.from(container.querySelectorAll("input"));
    const found = inputs.find(
      (i) => i.value === "原始标题" || i.value === "我改了一半的标题",
    );
    if (!found) throw new Error("找不到标题输入框");
    return found;
  }

  it("改了标题未保存时 refetch 返回同 id 新对象 → 输入必须保留", async () => {
    const { CourseProjectDetail, qc } = await renderDetail();

    // 用户开始编辑标题
    const input = titleInput();
    await act(async () => {
      setNativeValue(input, "我改了一半的标题");
    });
    expect(titleInput().value).toBe("我改了一半的标题");

    // 模拟 invalidateQueries 后的 refetch：**同 id、内容相同、但是新对象引用**
    // （真实代码里 .find() 每次都返回新 derive 的对象）
    h.projects = [makeProject()];
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <CourseProjectDetail projectId="cp_abc123def456" onBack={vi.fn()} />
        </QueryClientProvider>,
      );
    });

    expect(
      titleInput().value,
      "refetch 不得把用户未保存的输入打回服务端值",
    ).toBe("我改了一半的标题");
  });

  it("切换到另一个项目（id 变）时必须重新同步，不得串数据", async () => {
    const { CourseProjectDetail, qc } = await renderDetail();

    const input = titleInput();
    await act(async () => {
      setNativeValue(input, "我改了一半的标题");
    });

    // 换项目：id 不同 → 必须加载新项目的值
    h.projects = [
      makeProject(),
      makeProject({ id: "cp_999888777666", title: "另一个项目" }),
    ];
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <CourseProjectDetail projectId="cp_999888777666" onBack={vi.fn()} />
        </QueryClientProvider>,
      );
    });

    const values = Array.from(container.querySelectorAll("input")).map(
      (i) => i.value,
    );
    expect(
      values,
      "切项目必须重新同步，否则会把 A 项目的编辑写进 B 项目",
    ).toContain("另一个项目");
    expect(values).not.toContain("我改了一半的标题");
  });
});
