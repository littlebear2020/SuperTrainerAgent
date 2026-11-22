import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceSelector } from "@/components/workspace/voice-twin/voice-selector";
import type { PresetVoice } from "@/core/voice-twin/types";

const h = vi.hoisted(() => ({ voices: [] as PresetVoice[] }));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: { voiceTwin: { selectVoice: "选择音色" } } }),
}));

vi.mock("@/core/voice-twin/hooks", () => ({
  useVoiceTwins: () => ({ data: [], isError: false }),
  usePresetVoices: () => ({ data: h.voices, isLoading: false, isError: false }),
  useVoicePreview: () => ({
    playingId: null,
    loadingId: null,
    toggle: vi.fn(),
  }),
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement | null = null;

async function render(value: string) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(<VoiceSelector value={value} onChange={vi.fn()} />);
  });
  return container.querySelector("button[role='combobox']")!;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  root = null;
  container = null;
});

describe("VoiceSelector 当前值", () => {
  it("退役值保留原 ID 并标停用，不冒充未选择或自动换声", async () => {
    const trigger = await render("preset:146");
    expect(trigger.textContent).toContain("preset:146");
    expect(trigger.textContent).toContain("已停用");
    expect(trigger.textContent).not.toBe("选择音色");
  });

  it("目录内字节值仍显示名称", async () => {
    h.voices = [
      {
        id: "doubao:zh_female_vv_uranus_bigtts",
        name: "Vivi2.0",
        gender: "female",
        locale: "zh-CN",
        provider: "doubao",
        description: "高自然度女声",
        scenario: ["培训讲解"],
        tone: ["知性"],
      },
    ];
    const trigger = await render("doubao:zh_female_vv_uranus_bigtts");
    expect(trigger.textContent).toContain("Vivi2.0");
    expect(trigger.textContent).not.toContain("已停用");
  });
});
