import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const h = vi.hoisted(() => ({
  updateConfigMut: { mutate: vi.fn(), isPending: false },
}));

const DOUBAO_VOICE = "doubao:zh_female_vv_uranus_bigtts";

vi.mock("@/core/video-gen/hooks", () => ({
  useVideoVoices: () => ({
    data: [
      {
        id: "preset:332",
        name: "旧 360 男声",
        gender: "Male",
        provider: "qihu",
      },
      {
        id: "preset:146",
        name: "旧 360 女声",
        gender: "Female",
        provider: "qihu",
      },
      {
        id: DOUBAO_VOICE,
        name: "Vivi2.0",
        gender: "Female",
        provider: "doubao",
      },
      {
        id: "zh-CN-YunjianNeural",
        name: "旧 Edge 男声",
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

function sb(videoType: string, voiceId: string | null) {
  return {
    title: "t",
    config: {
      video_type: videoType,
      width: 1080,
      height: 1920,
      voice_id: voiceId,
      tts_speed: 1.0,
    },
    frames: [{ index: 0, narration: "一", duration: 2 }],
  } as unknown as Parameters<typeof AudioSettingsSection>[0]["storyboard"];
}

function setup(
  storyboard: Parameters<typeof AudioSettingsSection>[0]["storyboard"],
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root: Root = createRoot(container);
  const qc = new QueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <AudioSettingsSection
          taskId="t1"
          storyboard={storyboard}
          taskStatus="draft"
        />
      </QueryClientProvider>,
    );
  });
  return { container, root };
}

function expandAndGetSelect(container: HTMLElement) {
  const toggle = container.querySelector("button[title='展开音频设置']")!;
  void act(() =>
    toggle.dispatchEvent(new MouseEvent("click", { bubbles: true })),
  );
  return container.querySelector<HTMLSelectElement>(
    "[data-testid='voice-select']",
  )!;
}

function saveButton(container: HTMLElement) {
  return Array.from(container.querySelectorAll("button")).find((button) =>
    button.textContent?.includes("保存音频设置"),
  )!;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("AudioSettingsSection 退役音色", () => {
  it.each(["vimax", "ai_image", "ai_video", "remotion_course"])(
    "%s 保留 preset 原值并标停用，旧目录不可选且保存禁用",
    (taskType) => {
      const { container } = setup(sb(taskType, "preset:332"));
      const select = expandAndGetSelect(container);
      const options = Array.from(select.options);
      expect(
        options.some((option) => option.textContent?.includes("旧 360 男声")),
      ).toBe(false);
      expect(
        options.some((option) => option.textContent?.includes("旧 360 女声")),
      ).toBe(false);
      expect(
        options.some((option) => option.textContent?.includes("旧 Edge 男声")),
      ).toBe(false);
      expect(
        options.some((option) => option.textContent?.includes("Vivi2.0")),
      ).toBe(true);
      const residual = options.find((option) => option.value === "preset:332");
      expect(residual).toBeDefined();
      expect(residual!.textContent).toContain("原音色已停用");
      expect(residual!.disabled).toBe(true);
      expect(select.value).toBe("preset:332");
      expect(saveButton(container).disabled).toBe(true);
    },
  );

  it("改选字节目录音色后可保存，不残留旧禁用态", () => {
    const { container } = setup(sb("ai_image", "preset:332"));
    const select = expandAndGetSelect(container);
    act(() => {
      select.value = DOUBAO_VOICE;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(saveButton(container).disabled).toBe(false);
  });
});
