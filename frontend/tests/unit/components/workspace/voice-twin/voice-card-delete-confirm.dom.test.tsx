/**
 * 弹层统一（OBS-3，2026-10-09）：voice-card 删除确认从 window.confirm 换 useConfirm 弹层。
 *
 * 契约（两分支）：
 * - 点删除 → 弹层出现（「确定要删除这条声线吗？此操作不可撤销。」）；
 * - 点取消 → deleteMutation.mutate 未被调用（无副作用）；
 * - 点确认 → mutate 恰被调用一次、参数为 voice.id。
 *
 * harness 参照 recent-chat-list-delete-confirm.test.tsx（仓库无 @testing-library/react，
 * createRoot + act + happy-dom 手写）。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  deleteVoice: vi.fn(),
  tBase: {} as Record<string, unknown>,
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: h.tBase }),
}));

vi.mock("@/core/theme/glass", () => ({
  useGlassStyle: () => ({ glassStyle: () => ({}) }),
}));

vi.mock("@/core/voice-twin/api", () => ({
  getDemoAudioURL: (id: string) => `/demo/${id}`,
}));

vi.mock("@/core/voice-twin/hooks", () => ({
  useDeleteVoiceTwin: () => ({ mutate: h.deleteVoice, isPending: false }),
}));

vi.mock("@/components/workspace/voice-twin/audio-player", () => ({
  AudioPlayer: () => null,
}));
vi.mock("@/components/workspace/voice-twin/synthesize-dialog", () => ({
  SynthesizeDialog: () => null,
}));
vi.mock("@/components/workspace/voice-twin/training-progress", () => ({
  TrainingProgress: () => null,
}));

import { DialogProvider } from "@/components/workspace/dialog-provider";
import { VoiceCard } from "@/components/workspace/voice-twin/voice-card";
import { zhCN } from "@/core/i18n/locales/zh-CN";
import type { VoiceTwin } from "@/core/voice-twin/types";

h.tBase = zhCN as unknown as Record<string, unknown>;

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const voice = {
  id: "vt_1",
  voice_name: "音色A",
  model_type: 1,
  language: 1,
  status: "ready",
  demo_available: false,
} as unknown as VoiceTwin;

function buttonsWithText(text: string): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll("button")).filter(
    (b) => b.textContent?.trim() === text,
  );
}

async function clickRadix(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("voice-card 删除确认弹层（OBS-3）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.deleteVoice.mockClear();
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

  async function renderCard() {
    await act(async () => {
      root.render(
        <DialogProvider>
          <VoiceCard voice={voice} />
        </DialogProvider>,
      );
    });
  }

  it("点删除 → 弹层出现；取消 → deleteMutation 未被调用", async () => {
    await renderCard();
    const deleteBtn = buttonsWithText(zhCN.voiceTwin.deleteButton)[0];
    expect(deleteBtn).toBeTruthy();
    await clickRadix(deleteBtn!);

    expect(document.body.textContent).toContain("确定要删除这条声线吗？");
    expect(h.deleteVoice).not.toHaveBeenCalled();

    const cancel = buttonsWithText("取消")[0];
    expect(cancel).toBeTruthy();
    await clickRadix(cancel!);
    expect(h.deleteVoice).not.toHaveBeenCalled();
  });

  it("点删除 → 确认 → deleteMutation 恰被调用一次（参数 = voice.id）", async () => {
    await renderCard();
    await clickRadix(buttonsWithText(zhCN.voiceTwin.deleteButton)[0]!);

    const confirm = buttonsWithText("确定")[0];
    expect(confirm).toBeTruthy();
    await clickRadix(confirm!);
    expect(h.deleteVoice).toHaveBeenCalledTimes(1);
    expect(h.deleteVoice).toHaveBeenCalledWith("vt_1", expect.anything());
  });
});
