import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CourseProjectDetailPage } from "@/components/workspace/video-gen/course-project-detail";
import type { CourseProject, VideoTaskBrief } from "@/core/video-gen/types";

const h = vi.hoisted(() => ({
  projects: [] as CourseProject[],
  tasks: [] as VideoTaskBrief[],
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

function stubMut() {
  return { mutate: vi.fn(), isPending: false };
}

vi.mock("@/core/video-gen/hooks", () => ({
  useCourseProjects: () => ({ data: h.projects, isLoading: false }),
  useUpdateCourseProject: () => ({ mutate: h.updateMutate, isPending: false }),
  useDeleteCourseProject: () => ({ mutate: h.deleteMutate, isPending: false }),
  useVideoTasks: () => ({ data: h.tasks, isLoading: false }),
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

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const STORED_PALETTE = {
  primary: "#112233",
  saturation: 1.25,
  contrast: 0.85,
  brightness: 0.15,
  grade: "legacy-grade",
  old_extension: 42,
} as const;

function storedProject(): CourseProject {
  return {
    id: "cp_existing",
    user_id: "u1",
    title: "原始标题",
    visual_style_preset: "flat",
    color_palette: { ...STORED_PALETTE },
    remotion_theme: "warm",
    font_family: "Georgia,serif",
    logo_path: "brand/existing-logo.png",
    intro_outro: {
      intro: { headline: "保留片头", subtitle: "保留副标题" },
      outro: { headline: "保留片尾", follow: "保留关注语" },
    },
    character_sheets: [],
    prop_assets: [],
    voice_id: "",
    tts_speed: null,
    subtitle_style: "default",
  };
}

function setNativeValue(element: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

function button(text: string): HTMLButtonElement {
  const result = Array.from(document.querySelectorAll("button")).find(
    (item) => item.textContent === text,
  );
  if (!result) throw new Error(`button not found: ${text}`);
  return result;
}

describe("simplified course project detail", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    h.projects = [storedProject()];
    h.tasks = [];
    h.updateMutate.mockReset();
    h.deleteMutate.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    queryClient.clear();
    container.remove();
    document.body.innerHTML = "";
  });

  async function renderDetail() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <CourseProjectDetailPage projectId="cp_existing" onBack={vi.fn()} />
        </QueryClientProvider>,
      );
    });
  }

  it("unmounts brand, color and related-video sections but keeps asset editing", async () => {
    await renderDetail();
    const text = container.textContent ?? "";
    for (const expected of [
      "基本设置",
      "角色参考图",
      "风格锚图",
      "道具资产",
      "保存",
    ]) {
      expect(text).toContain(expected);
    }
    for (const removed of ["品牌包", "色彩微调", "关联视频"]) {
      expect(text).not.toContain(removed);
    }
  });

  it("title-only saves preserve every stored palette and brand field", async () => {
    await renderDetail();
    const title = container.querySelector<HTMLInputElement>(
      "input[placeholder='如：AI 入门课程']",
    );
    if (!title) throw new Error("title input not found");
    await act(async () => setNativeValue(title, "仅改标题"));
    await act(async () => button("保存").click());

    expect(h.updateMutate).toHaveBeenCalledTimes(1);
    const call = h.updateMutate.mock.calls[0]?.[0] as {
      id: string;
      input: Record<string, unknown>;
    };
    expect(call.id).toBe("cp_existing");
    expect(call.input.title).toBe("仅改标题");
    expect(call.input.color_palette).toEqual({ ...STORED_PALETTE });
    for (const brandField of [
      "remotion_theme",
      "font_family",
      "logo_path",
      "intro_outro",
    ]) {
      expect(call.input).not.toHaveProperty(brandField);
    }
    expect(JSON.stringify(call.input)).not.toContain("dark-blue");
  });
});
