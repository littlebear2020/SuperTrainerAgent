/**
 * B1/B3: upload panel submits the user-selected clone model + language.
 *
 * Regression target: the panel used to hardcode `model_type: 4` and never
 * send `language`, even though the backend exposes 5 models via
 * GET /api/voice-twins/models and accepts language 0-7. These tests drive
 * the real component (file input → prescreen pass → select → submit) and
 * assert the exact mutation payload, plus the graceful fallback when the
 * models list fails to load (selector hidden, submit still works, default 5).
 *
 * i18n (2026-10-05 review): real I18nProvider + real locale tables — no
 * `useI18n` mock. The submit button is located by the RESOLVED
 * `voiceTwin.submitTraining` label, so a deleted/renamed locale key turns
 * this file red instead of silently passing on a Proxy-echo key lookup.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UploadPanel } from "@/components/workspace/voice-twin/upload-panel";
import { I18nProvider } from "@/core/i18n/context";
import { translations } from "@/core/i18n/translations";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({
  useVoiceTwinModels: vi.fn(),
  usePrescreenAudio: vi.fn(),
  useUploadVoiceTwin: vi.fn(),
  localPrescreen: vi.fn(),
}));

vi.mock("@/core/voice-twin/hooks", () => ({
  useVoiceTwinModels: () => mocks.useVoiceTwinModels(),
  usePrescreenAudio: () => mocks.usePrescreenAudio(),
  useUploadVoiceTwin: () => mocks.useUploadVoiceTwin(),
}));

vi.mock("@/core/voice-twin/prescreen", () => ({
  localPrescreen: (file: File) => mocks.localPrescreen(file),
}));

vi.mock("@/core/theme/glass", () => ({
  useGlassStyle: () => ({ glassStyle: () => ({}), isDark: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Heavy/browser-only children are irrelevant to the submit payload.
vi.mock("@/components/workspace/voice-twin/teleprompter", () => ({
  Teleprompter: () => null,
  DEFAULT_TELEPROMPTER_TEXTS: { zh: "提示文本", en: "prompt" },
}));
vi.mock("@/components/workspace/voice-twin/voice-recorder", () => ({
  VoiceRecorder: () => null,
  isRecordingSupported: () => false,
}));
vi.mock("@/components/workspace/voice-twin/waveform-preview", () => ({
  WaveformPreview: () => null,
}));

// Mirrors backend MODELS_INFO (schemas.py).
const MODELS = [
  {
    model_type: 1,
    name: "ICL 1.0",
    description: "标准音色克隆，适合正式课件朗读",
    languages: [0, 1, 2, 3, 4, 5],
    resource_id: "seed-icl-1.0",
  },
  {
    model_type: 2,
    name: "DiT 标准版",
    description: "音色复刻，不还原风格",
    languages: [0, 1, 2, 3, 4, 5, 6, 7],
    resource_id: "seed-icl-1.0",
  },
  {
    model_type: 3,
    name: "DiT 还原版",
    description: "音色+口音+语速风格还原",
    languages: [0, 1],
    resource_id: "seed-icl-1.0",
  },
  {
    model_type: 4,
    name: "ICL 2.0",
    description: "第二代音色克隆",
    languages: [0, 1],
    resource_id: "seed-icl-2.0",
  },
  {
    model_type: 5,
    name: "ICL 3.0",
    description: "最新音色克隆，高逼真度高自然度",
    languages: [0, 1],
    resource_id: "seed-icl-2.0",
  },
];

const PASS_RESULT = {
  status: "pass",
  temp_file_id: "tmp-1",
  duration: 30,
  format: "wav",
  needs_transcode: false,
  warnings: [],
};

function setInputValue(
  el: HTMLInputElement | HTMLSelectElement,
  value: string,
) {
  const proto =
    el instanceof HTMLSelectElement ? HTMLSelectElement : HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(
    proto.prototype,
    "value",
  )?.set?.bind(el);
  setter?.(value);
  el.dispatchEvent(
    new Event(el instanceof HTMLSelectElement ? "change" : "input", {
      bubbles: true,
    }),
  );
}

describe("UploadPanel model/language selection (B1/B3)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let uploadMutate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    // happy-dom may lack blob URLs; the panel only stores/revokes them.
    // 结构类型化避开 unbound-method（URL 静态方法非 this:void 声明）。
    const urlApi = URL as unknown as {
      createObjectURL?: (b: Blob) => string;
      revokeObjectURL?: (u: string) => void;
    };
    urlApi.createObjectURL ??= () => "blob:mock";
    urlApi.revokeObjectURL ??= () => undefined;

    uploadMutate = vi.fn();
    mocks.useVoiceTwinModels.mockReturnValue({ data: MODELS, isError: false });
    mocks.usePrescreenAudio.mockReturnValue({
      mutateAsync: vi.fn(async () => PASS_RESULT),
      isError: false,
      isPending: false,
      error: null,
    });
    mocks.useUploadVoiceTwin.mockReturnValue({
      mutate: uploadMutate,
      isPending: false,
      isError: false,
      error: null,
    });
    mocks.localPrescreen.mockResolvedValue({
      valid: true,
      errors: [],
      warnings: [],
      duration: 30,
    });

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  /** Render and push the panel past the prescreen phase with a valid file. */
  async function reachForm() {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <UploadPanel />
        </I18nProvider>,
      );
    });
    const fileInput =
      container.querySelector<HTMLInputElement>('input[type="file"]');
    if (!fileInput) throw new Error("file input did not render");
    const file = new File([new Uint8Array(1024)], "a.wav", {
      type: "audio/wav",
    });
    Object.defineProperty(fileInput, "files", {
      value: [file],
      configurable: true,
    });
    await act(async () => {
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const modelSelect = container.querySelector(
      '[data-testid="model-type-select"]',
    );
    const langSelect = container.querySelector(
      '[data-testid="language-select"]',
    );
    if (!langSelect) throw new Error("form phase did not render");
    return { modelSelect, langSelect };
  }

  async function fillAndSubmit() {
    const textInputs =
      container.querySelectorAll<HTMLInputElement>('input[type="text"]');
    const [nameInput, speakerInput] = Array.from(textInputs);
    if (!nameInput || !speakerInput)
      throw new Error("name/speaker inputs did not render");
    await act(async () => {
      setInputValue(nameInput, "我的声音");
      setInputValue(speakerInput, "S_test123");
    });
    // Locate the submit button by the RESOLVED label from the real locale
    // tables (every locale's wording accepted). A deleted/renamed
    // `voiceTwin.submitTraining` key breaks compilation of this file; the old
    // Proxy-echo mock would have kept the test green regardless.
    const submitLabels = Object.values(translations).map(
      (tr) => tr.voiceTwin.submitTraining,
    );
    const submit = Array.from(container.querySelectorAll("button")).find((b) =>
      submitLabels.some((label) => b.textContent?.includes(label)),
    );
    if (!submit) throw new Error("submit button did not render");
    await act(async () => {
      submit.click();
    });
  }

  it("defaults to model 5 (ICL 3.0) and language 0 (中文)", async () => {
    await reachForm();
    await fillAndSubmit();

    expect(uploadMutate).toHaveBeenCalledTimes(1);
    const params = uploadMutate.mock.calls[0]?.[0];
    expect(params).toMatchObject({
      temp_file_id: "tmp-1",
      voice_name: "我的声音",
      speaker_id: "S_test123",
      model_type: 5,
      language: 0,
    });
  });

  it("submits the user-selected model_type and language", async () => {
    const { modelSelect, langSelect } = await reachForm();
    if (!modelSelect) throw new Error("model selector did not render");
    await act(async () => {
      setInputValue(modelSelect as HTMLSelectElement, "2");
      setInputValue(langSelect as HTMLSelectElement, "7");
    });
    await fillAndSubmit();

    const params = uploadMutate.mock.calls[0]?.[0];
    expect(params.model_type).toBe(2);
    expect(params.language).toBe(7);
  });

  it("renders each model with its backend description", async () => {
    const { modelSelect } = await reachForm();
    const options = Array.from(
      (modelSelect as HTMLSelectElement).querySelectorAll("option"),
    ).map((o) => o.textContent);

    expect(options).toHaveLength(5);
    expect(options[0]).toContain("ICL 1.0");
    expect(options[0]).toContain("标准音色克隆");
    expect(options[4]).toContain("ICL 3.0");
    expect(options[4]).toContain("高逼真度");
  });

  it("warns (without blocking) when the model lacks the chosen language", async () => {
    const { modelSelect, langSelect } = await reachForm();
    // NOTE (2026-10-05 review): this warning is HARDCODED Chinese in
    // upload-panel.tsx — it bypasses i18n, so an English UI would still show
    // zh text. Known production gap, deliberately NOT fixed in this
    // test-only batch; the assertions below pin the literal string as-is.
    expect(container.textContent).not.toContain("该模型不支持所选语言");

    // ICL 3.0 (default) only supports languages [0, 1] → 日本語 triggers hint.
    await act(async () => {
      setInputValue(langSelect as HTMLSelectElement, "2");
    });
    expect(container.textContent).toContain("该模型不支持所选语言");

    // DiT 标准版 supports all 8 → hint clears, and it is a hint not a block.
    await act(async () => {
      setInputValue(modelSelect as HTMLSelectElement, "2");
    });
    expect(container.textContent).not.toContain("该模型不支持所选语言");
    await fillAndSubmit();
    expect(uploadMutate).toHaveBeenCalledTimes(1);
  });

  it("falls back to model 5 with no selector when the models list fails", async () => {
    mocks.useVoiceTwinModels.mockReturnValue({
      data: undefined,
      isError: true,
    });
    const { modelSelect } = await reachForm();

    expect(modelSelect).toBeNull();
    await fillAndSubmit();
    expect(uploadMutate).toHaveBeenCalledTimes(1);
    expect(uploadMutate.mock.calls[0]?.[0].model_type).toBe(5);
  });
});
