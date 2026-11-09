/**
 * Plan B Task 4（2026-10-19）：DialogVoiceRow 的 ViMax-only 行为。
 *
 * 契约（plan Task 4 Step 1）：
 * - vimax：下拉不出现「360 声音（高质量）」分组；ai_image 仍出现。
 * - vimax + 残留 preset：显示「ViMax 不可用」提示 + 试听按钮禁用（不静默清空）。
 * - ai_image + preset：无阻断提示、试听可用（语义不变）。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

import { DialogVoiceRow } from "@/components/workspace/video-gen/video-gen-create-dialog";

const VOICES = [
  { id: "preset:332", name: "磁性解说男声", gender: "Male", provider: "qihu" },
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
];

function setup(taskType: string, voiceId: string | null) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <DialogVoiceRow
        taskType={taskType as "ai_image"}
        mode="fixed"
        script="脚本"
        voiceId={voiceId}
        setVoiceId={vi.fn()}
        lang="zh"
        courseProjectId={undefined}
        playingAudio={null}
        togglePreview={vi.fn()}
        voicesQuery={{ data: VOICES } as unknown as never}
        previewVoiceId={voiceId ?? "doubao:zh_male_A"}
      />,
    );
  });
  return container;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("DialogVoiceRow ViMax-only", () => {
  it("选中 preset:332：ai_image trigger 显示原名称；vimax 显示 ViMax 不可用（残留不静默清空）", () => {
    // ai_image：preset 是合法选项 → trigger 正常显示音色名
    const c2 = setup("ai_image", "preset:332");
    expect(c2.textContent).toContain("磁性解说男声");
    document.body.innerHTML = "";

    // vimax：同一选中值以「不可用」残留项呈现（保留原值、标记禁用）
    const c1 = setup("vimax", "preset:332");
    expect(c1.textContent).toContain("ViMax 不可用");
    expect(c1.textContent).not.toContain("磁性解说男声");
  });

  it("vimax + 残留 preset：提示可行动文案 + 试听禁用", () => {
    const c = setup("vimax", "preset:332");
    const hint = c.querySelector("[data-testid='vimax-preset-blocked-hint']");
    expect(hint).not.toBeNull();
    expect(hint!.textContent).toContain("ViMax");
    const buttons = Array.from(c.querySelectorAll("button"));
    const preview = buttons.find((b) => b.title === "试听配音");
    expect(preview).toBeDefined();
    expect(preview!.disabled).toBe(true);
    expect(c.textContent).toContain("ViMax 不可用");
  });

  it("ai_image + preset：无阻断提示、试听可用", () => {
    const c = setup("ai_image", "preset:332");
    expect(
      c.querySelector("[data-testid='vimax-preset-blocked-hint']"),
    ).toBeNull();
    const buttons = Array.from(c.querySelectorAll("button"));
    const preview = buttons.find((b) => b.title === "试听配音");
    expect(preview!.disabled).toBe(false);
  });
});

describe("DialogVoiceRow 试听 URL 的 task_type", () => {
  it("vimax：试听 URL 携带 task_type=vimax；ai_image：不携带", () => {
    const urls: string[] = [];
    const toggle = (u: string) => urls.push(u);
    const mk = (taskType: string) => {
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
            voiceId="doubao:zh_male_A"
            setVoiceId={vi.fn()}
            lang="zh"
            courseProjectId={undefined}
            playingAudio={null}
            togglePreview={toggle}
            voicesQuery={{ data: VOICES } as unknown as never}
            previewVoiceId="doubao:zh_male_A"
          />,
        );
      });
      const btn = Array.from(container.querySelectorAll("button")).find(
        (b) => b.title === "试听配音",
      )!;
      act(() => {
        btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
    };
    mk("vimax");
    expect(urls[0]).toContain("task_type=vimax");
    mk("ai_image");
    expect(urls[1]).not.toContain("task_type");
  });
});
