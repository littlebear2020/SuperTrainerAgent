import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));
vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light" }),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
  getLangGraphBaseURL: () => "",
}));
vi.mock("@/core/models/hooks", () => ({ useModels: vi.fn() }));
vi.mock("@/core/skills/hooks", () => ({
  useSkills: () => ({ skills: [], isLoading: false }),
}));
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { InputBox } from "@/components/workspace/input-box";
import { ThreadContext } from "@/components/workspace/messages/context";
import { I18nProvider } from "@/core/i18n/context";
import type { Locale } from "@/core/i18n/locale";
import { enUS, zhCN } from "@/core/i18n/locales";
import { useModels } from "@/core/models/hooks";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

type InputBoxProps = Parameters<typeof InputBox>[0];
type OnContextChangeProp = InputBoxProps["onContextChange"];

beforeEach(() => {
  (useModels as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    models: [
      {
        name: "m1",
        display_name: "模型一",
        model: "m1",
        supports_thinking: false,
      },
    ],
    modeDefaults: {},
    tokenUsageEnabled: false,
    isLoading: false,
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

async function renderInputBox(
  locale: Locale = "en-US",
  onContextChange = vi.fn(),
) {
  document.cookie = `locale=${locale}; path=/`;
  await act(async () => {
    root.render(
      <I18nProvider initialLocale={locale}>
        <ThreadContext.Provider value={{ thread: { messages: [] } as never }}>
          <PromptInputProvider initialInput="">
            <InputBox
              threadId="t1"
              context={{ model_name: "m1", mode: "flash" }}
              onContextChange={
                onContextChange as unknown as OnContextChangeProp
              }
            />
          </PromptInputProvider>
        </ThreadContext.Provider>
      </I18nProvider>,
    );
  });
  return onContextChange;
}

async function openModeMenu(locale: Locale) {
  const label =
    locale === "zh-CN" ? zhCN.inputBox.flashMode : enUS.inputBox.flashMode;
  const trigger = Array.from(
    container.querySelectorAll<HTMLButtonElement>("button"),
  ).find((button) => button.textContent?.includes(label));
  expect(trigger).toBeTruthy();
  await act(async () => {
    trigger!.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    trigger!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function menuItems(): HTMLElement[] {
  return Array.from(
    document.body.querySelectorAll<HTMLElement>("[role='menuitem']"),
  );
}

describe("models without thinking still support standard mode", () => {
  it("shows no separate thinking mode in Chinese", async () => {
    await renderInputBox("zh-CN");
    await openModeMenu("zh-CN");

    expect(menuItems()).toHaveLength(3);
    expect(document.body.textContent).not.toContain("推理深度");
    expect(
      menuItems().some((item) => item.textContent?.includes("思考后再行动")),
    ).toBe(false);
  });

  it("shows no separate thinking mode or effort menu in English", async () => {
    await renderInputBox("en-US");
    await openModeMenu("en-US");

    expect(menuItems()).toHaveLength(3);
    expect(document.body.textContent).not.toContain("Reasoning Effort");
    expect(document.body.textContent).not.toContain("Reasoning before action");
  });

  it("selects standard without requiring thinking support or persisting effort", async () => {
    const onContextChange = await renderInputBox("en-US");
    onContextChange.mockClear();
    await openModeMenu("en-US");
    const standard = menuItems().find((item) =>
      item.textContent?.includes(enUS.inputBox.standardModeDescription),
    );
    expect(standard).toBeTruthy();

    await act(async () => {
      standard!.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
      standard!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(onContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_name: "m1", mode: "standard" }),
    );
    expect(onContextChange.mock.lastCall?.[0]).not.toHaveProperty(
      "reasoning_effort",
    );
  });
});
