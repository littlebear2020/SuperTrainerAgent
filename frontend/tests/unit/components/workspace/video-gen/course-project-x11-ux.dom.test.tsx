/**
 * X11（2026-08-27 计划）：资产页 UX 包。
 *
 * - TC-X11-01：角色区标题/添加入口/空态 + 「角色参考」失实区块不存在 +
 *   保存载荷不含 character_reference（方案 B 用户拍板删除）
 * - X11-2：合成图缩略图（有 composite_url → serve URL；无 → 占位文案）
 * - TC-X11-03：道具区说明文案（多道具/≤2/每帧选 1 张不拼图 + description placeholder）
 *
 * 沿用 course-project-dirty-guard.test.tsx 的 createRoot + act + happy-dom 手写 harness。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

function setNativeValue(el: HTMLInputElement, value: string) {
  const desc = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  );
  desc?.set?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function makeProject(over: Record<string, unknown> = {}) {
  return {
    id: "cp_x11ux000001",
    title: "X11 项目",
    visual_style_preset: "flat",
    character_reference: "存量文本描述（后端字段保留，前端不再提交）",
    character_sheets: [],
    prop_assets: [],
    ...over,
  };
}

describe("X11: 资产页 UX 包", () => {
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

  async function renderDetail(projectId = "cp_x11ux000001") {
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
  }

  it("TC-X11-01: 角色区标题/副标题/添加入口/空态 + 「角色参考」区块不存在", async () => {
    await renderDetail();
    const text = container.textContent ?? "";

    // 标题去单数「（正面）」→「角色参考图」；副标题补多角色与 3 人同框
    expect(text).toContain("角色参考图");
    expect(text).not.toContain("角色参考图（正面）");
    expect(text).toContain("可添加多个角色");
    expect(text).toContain("3 人同框");

    // 生成表单分组标题（默认态=添加新角色）+ 空态引导
    expect(text).toContain("添加新角色");
    expect(text).toContain("暂无角色");

    // 「角色参考」失实区块（textarea + guidance）已删除（方案 B）
    expect(
      Array.from(container.querySelectorAll("h2")).some(
        (e) => e.textContent === "角色参考",
      ),
    ).toBe(false);
    const tas = Array.from(container.querySelectorAll("textarea"));
    expect(tas.some((t) => t.placeholder.includes("一位戴眼镜"))).toBe(false);
    expect(text).not.toContain("仅文本描述时跨镜人物一致性较弱");
  });

  it("TC-X11-01: handleSave 载荷不含 character_reference", async () => {
    await renderDetail();
    const titleInput = Array.from(container.querySelectorAll("input")).find(
      (i) => i.value === "X11 项目",
    );
    expect(titleInput).toBeTruthy();
    await act(async () => {
      setNativeValue(titleInput!, "改过的标题");
    });
    const saveBtn = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("保存"),
    );
    expect(saveBtn).toBeTruthy();
    await act(async () => {
      saveBtn!.click();
    });
    expect(h.updateMutate).toHaveBeenCalledTimes(1);
    const payload = h.updateMutate.mock.calls[0]![0] as {
      input: Record<string, unknown>;
    };
    expect(payload.input).toBeTruthy();
    expect(payload.input.title).toBe("改过的标题");
    expect("character_reference" in payload.input).toBe(false);
  });

  it("X11-2: 有 composite_url → serve URL 缩略图；无 → 占位文案", async () => {
    h.projects = [
      makeProject({
        character_sheets: [
          {
            id: "aaaa1111",
            name: "小明",
            front_url: "/f.png",
            side_url: "/s.png",
            back_url: "/b.png",
            composite_url: "/storage/character_sheets/p1/composite_1.png",
            features: "",
          },
          {
            id: "bbbb2222",
            name: "小红",
            front_url: "/f2.png",
            side_url: "/s2.png",
            back_url: "/b2.png",
            features: "",
          },
        ],
      }),
    ];
    await renderDetail();
    const imgs = Array.from(container.querySelectorAll("img"));
    expect(
      imgs.some((i) =>
        i.getAttribute("src")?.includes("/character-sheets/aaaa1111/composite"),
      ),
      "有 composite_url 的角色卡须展示合成图缩略图（serve URL）",
    ).toBe(true);
    const text = container.textContent ?? "";
    expect(text).toContain("合成图生成后展示");
    // 小红（无 composite_url）不得有 composite serve 请求
    expect(
      imgs.some((i) =>
        i.getAttribute("src")?.includes("/character-sheets/bbbb2222/composite"),
      ),
    ).toBe(false);
  });

  it("X11-1: 重生成态分组标题「重新生成：{name}」+ 取消回添加新角色", async () => {
    h.projects = [
      makeProject({
        character_sheets: [
          {
            id: "aaaa1111",
            name: "小明",
            front_url: "/f.png",
            side_url: "/s.png",
            back_url: "/b.png",
            features: "红发骑士",
            composite_url: "/c.png",
          },
        ],
      }),
    ];
    await renderDetail();
    const text = () => container.textContent ?? "";
    expect(text()).not.toContain("暂无角色");

    const pencil = Array.from(container.querySelectorAll("button")).find(
      (b) => b.title === "用原描述预填并重新生成",
    );
    expect(pencil).toBeTruthy();
    await act(async () => {
      pencil?.click();
    });
    // 分组标题（h3）本体切到重生成态；「取消改回添加新角色」按钮文案包含子串
    // 「添加新角色」属预期——断言锚定 h3 而非全文子串
    const groupTitle = () =>
      Array.from(container.querySelectorAll("h3"))
        .map((e) => e.textContent ?? "")
        .join("|");
    expect(text()).toContain("重新生成：小明");
    expect(groupTitle()).not.toContain("添加新角色");

    const cancel = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("取消改回添加新角色"),
    );
    expect(cancel).toBeTruthy();
    // 复核 3.4（2026-08-27）：取消前角色名已预填；取消后须清空——修前残留会撞 M1 重名 409
    const nameInput = () =>
      container.querySelector<HTMLInputElement>(
        'input[placeholder="角色名（如：小明）"]',
      );
    expect(nameInput()?.value).toBe("小明");
    await act(async () => {
      cancel?.click();
    });
    expect(groupTitle()).toContain("添加新角色");
    expect(groupTitle()).not.toContain("重新生成：小明");
    expect(nameInput()?.value).toBe("");
  });

  it("TC-X11-03: 道具区说明文案（多道具/≤2/每帧选 1 张不拼图）", async () => {
    await renderDetail();
    const text = container.textContent ?? "";
    expect(text).toContain("可添加多个道具");
    expect(text).toContain("最多出现 2 个");
    expect(text).toContain("不拼图");
    const descInput = Array.from(container.querySelectorAll("input")).find(
      (i) => i.placeholder.includes("帮助 AI 识别"),
    );
    expect(descInput).toBeTruthy();
    expect(descInput?.placeholder).toContain("帮助 AI 识别旁白中的道具称呼");
  });
});
