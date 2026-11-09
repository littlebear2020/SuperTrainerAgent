/**
 * Plan B Task 4（2026-10-19）：AudioSettingsSection 的 ViMax-only 行为。
 *
 * 契约（plan Task 4 Step 1）：
 * - vimax 任务：音色下拉不出现 360（qihu）条目；残留 preset 当前值显示为
 *   「ViMax 不可用」的禁用 option；选中 preset 时保存按钮禁用。
 * - 非 vimax 任务：qihu 条目照常出现，preset 可选可保存（语义不变）。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const h = vi.hoisted(() => ({
  updateConfigMut: { mutate: vi.fn(), isPending: false },
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useVideoVoices: () => ({
    data: [
      {
        id: "preset:332",
        name: "磁性解说男声",
        gender: "Male",
        provider: "qihu",
      },
      {
        id: "preset:146",
        name: "知性女声",
        gender: "Female",
        provider: "qihu",
      },
      {
        id: "doubao:zh_male_A",
        name: "豆包男声",
        gender: "Male",
        provider: "doubao",
      },
      {
        id: "zh-CN-YunjianNeural",
        name: "云健",
        gender: "Male",
        provider: "edge-tts",
      },
    ],
  }),
  useUpdateStoryboardConfig: () => h.updateConfigMut,
  useRegenerateAllAudio: () => ({ mutate: vi.fn(), isPending: false }),
  useRecomposeTask: () => ({ mutate: vi.fn() }),
  useReRenderTask: () => ({ mutate: vi.fn() }),
  STORYBOARD_KEY: "videoStoryboard",
}));

vi.mock("@/core/video-gen/api", () => ({ getStoryboard: vi.fn() }));

import { AudioSettingsSection } from "@/components/workspace/video-gen/video-gen-detail-audio-settings";

function sb(video_type: string, voice_id: string | null) {
  return {
    title: "t",
    config: { video_type, width: 1080, height: 1920, voice_id, tts_speed: 1.0 },
    frames: [{ index: 0, narration: "一", duration: 2 }],
  } as unknown as Parameters<typeof AudioSettingsSection>[0]["storyboard"];
}

function setup(
  storyboard: Parameters<typeof AudioSettingsSection>[0]["storyboard"],
  taskStatus = "draft",
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const qc = new QueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <AudioSettingsSection
          taskId="t1"
          storyboard={storyboard}
          taskStatus={taskStatus}
        />
      </QueryClientProvider>,
    );
  });
  return { container, root };
}

function expandAndGetSelect(container: HTMLElement) {
  const toggle = container.querySelector("button[title='展开音频设置']")!;
  act(() => {
    toggle.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  return container.querySelector<HTMLSelectElement>(
    "[data-testid='voice-select']",
  )!;
}

function saveButton(container: HTMLElement) {
  const btns = Array.from(container.querySelectorAll("button"));
  return btns.find((b) => b.textContent?.includes("保存音频设置"))!;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("AudioSettingsSection ViMax-only", () => {
  it("vimax：下拉无 360 条目；残留 preset 显示为 ViMax 不可用且保存禁用", () => {
    const { container } = setup(sb("vimax", "preset:332"));
    const select = expandAndGetSelect(container);
    const options = Array.from(select.options).map((o) => o.textContent ?? "");
    expect(options.some((t) => t.includes("磁性解说男声"))).toBe(false);
    expect(options.some((t) => t.includes("知性女声"))).toBe(false);
    expect(options.some((t) => t.includes("豆包男声"))).toBe(true);
    const residual = Array.from(select.options).find(
      (o) => o.value === "preset:332",
    );
    expect(residual).toBeDefined();
    expect(residual!.textContent).toContain("ViMax 不可用");
    expect(residual!.disabled).toBe(true);
    // preset 为当前值 → 保存禁用
    expect(saveButton(container).disabled).toBe(true);
  });

  it("vimax：改选豆包后保存可用（残留 preset 可被替换）", () => {
    const { container } = setup(sb("vimax", "preset:332"));
    const select = expandAndGetSelect(container);
    act(() => {
      select.value = "doubao:zh_male_A";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(saveButton(container).disabled).toBe(false);
  });

  it("ai_image：qihu 条目照常出现、preset 可选（非 ViMax 语义不变）", () => {
    const { container } = setup(sb("ai_image", null));
    const select = expandAndGetSelect(container);
    const options = Array.from(select.options).map((o) => o.textContent ?? "");
    expect(options.some((t) => t.includes("磁性解说男声"))).toBe(true);
    act(() => {
      select.value = "preset:332";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(saveButton(container).disabled).toBe(false);
  });
});
