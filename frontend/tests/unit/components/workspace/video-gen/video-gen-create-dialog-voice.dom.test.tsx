import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

import { DialogVoiceRow } from "@/components/workspace/video-gen/video-gen-create-dialog";

const DOUBAO_VOICE = "doubao:zh_female_vv_uranus_bigtts";
const VOICES = [
  { id: "preset:332", name: "旧 360 男声", gender: "Male", provider: "qihu" },
  { id: DOUBAO_VOICE, name: "Vivi2.0", gender: "Female", provider: "doubao" },
  {
    id: "zh-CN-YunjianNeural",
    name: "旧 Edge 男声",
    gender: "Male",
    provider: "edge-tts",
  },
];

function setup(
  taskType: string,
  voiceId: string | null,
  resolvedPreviewVoiceId?: string,
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const setVoiceId = vi.fn();
  const togglePreview = vi.fn();
  act(() => {
    root.render(
      <DialogVoiceRow
        taskType={taskType as "ai_image"}
        mode="fixed"
        script="脚本"
        voiceId={voiceId}
        setVoiceId={setVoiceId}
        lang="zh"
        courseProjectId={undefined}
        playingAudio={null}
        togglePreview={togglePreview}
        voicesQuery={{ data: VOICES } as unknown as never}
        previewVoiceId={resolvedPreviewVoiceId ?? voiceId ?? DOUBAO_VOICE}
      />,
    );
  });
  return { container, setVoiceId, togglePreview };
}

function previewButton(container: HTMLElement): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find(
    (item) => item.title === "试听配音",
  );
  if (!button) throw new Error("preview button not found");
  return button;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("DialogVoiceRow 退役音色", () => {
  it.each(["vimax", "ai_image", "ai_video", "remotion_course"])(
    "%s 保留 preset 原值、标停用并禁用试听，不自动改值",
    (taskType) => {
      const { container, setVoiceId } = setup(taskType, "preset:332");
      const hint = container.querySelector(
        "[data-testid='retired-voice-blocked-hint']",
      );
      expect(hint).not.toBeNull();
      expect(hint!.textContent).toContain("preset:332");
      expect(hint!.textContent).toContain("原音色已停用");
      expect(previewButton(container).disabled).toBe(true);
      expect(setVoiceId).not.toHaveBeenCalled();
    },
  );

  it("裸 Edge 残留也显示停用原因并禁用试听", () => {
    const { container } = setup("ai_image", "zh-CN-YunjianNeural");
    expect(container.textContent).toContain("zh-CN-YunjianNeural");
    expect(container.textContent).toContain("原音色已停用");
    expect(previewButton(container).disabled).toBe(true);
  });

  it("非中文残留没有默认音色时禁用试听，不落中文音色", () => {
    const { container } = setup("ai_image", null, "");
    expect(previewButton(container).disabled).toBe(true);
  });

  it("字节目录音色正常试听，无停用提示", () => {
    const { container, togglePreview } = setup("ai_image", DOUBAO_VOICE);
    expect(
      container.querySelector("[data-testid='retired-voice-blocked-hint']"),
    ).toBeNull();
    const preview = previewButton(container);
    expect(preview.disabled).toBe(false);
    act(() => preview.click());
    expect(togglePreview).toHaveBeenCalledWith(
      expect.stringContaining(encodeURIComponent(DOUBAO_VOICE)),
    );
  });
});

describe("DialogVoiceRow 试听 URL 的 task_type", () => {
  it("vimax 携带 task_type；ai_image 不携带", () => {
    const urls: string[] = [];
    for (const taskType of ["vimax", "ai_image"]) {
      document.body.innerHTML = "";
      const container = document.createElement("div");
      document.body.appendChild(container);
      const root = createRoot(container);
      act(() => {
        root.render(
          <DialogVoiceRow
            taskType={taskType as "ai_image"}
            mode="fixed"
            script="脚本"
            voiceId={DOUBAO_VOICE}
            setVoiceId={vi.fn()}
            lang="zh"
            courseProjectId={undefined}
            playingAudio={null}
            togglePreview={(url) => urls.push(url)}
            voicesQuery={{ data: VOICES } as unknown as never}
            previewVoiceId={DOUBAO_VOICE}
          />,
        );
      });
      act(() => previewButton(container).click());
    }
    expect(urls[0]).toContain("task_type=vimax");
    expect(urls[1]).not.toContain("task_type");
  });
});
