/**
 * T25（2026-08-13，决策 D6）：thinking 开关对不支持深度思考的模型
 * 不再静默失效。旧行为：菜单项直接消失，用户不知道为什么没有「思考」。
 * 新行为：菜单项保留但置灰，描述换成「当前模型不支持深度思考，可切换到
 * 豆包/通义等模型」——用户能看懂原因并知道怎么切换。
 */

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

vi.mock("@/core/models/hooks", () => ({
  useModels: vi.fn(),
}));

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

const fakeThread = { messages: [] } as never;

let container: HTMLDivElement;
let root: Root;

function mockModel(supportsThinking: boolean) {
  (useModels as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    models: [
      {
        name: "m1",
        display_name: "模型一",
        model: "m1",
        supports_thinking: supportsThinking,
      },
    ],
    tokenUsageEnabled: false,
    isLoading: false,
  });
}

async function renderInputBox(locale: Locale = "en-US") {
  await act(async () => root.unmount());
  root = createRoot(container);
  document.cookie = `locale=${locale}; path=/`;
  await act(async () => {
    root.render(
      <I18nProvider initialLocale={locale}>
        <ThreadContext.Provider value={{ thread: fakeThread }}>
          <PromptInputProvider initialInput="">
            <InputBox
              threadId="t1"
              context={{ model_name: "m1", mode: "flash" }}
            />
          </PromptInputProvider>
        </ThreadContext.Provider>
      </I18nProvider>,
    );
  });
}

function modeTrigger(locale: Locale): HTMLButtonElement {
  const label =
    locale === "zh-CN" ? zhCN.inputBox.flashMode : enUS.inputBox.flashMode;
  const buttons = Array.from(
    document.querySelectorAll<HTMLButtonElement>("button"),
  );
  const trigger = buttons.find((b) => b.textContent?.includes(label));
  if (!trigger) throw new Error("mode trigger not rendered");
  return trigger;
}

async function openMenu(locale: Locale) {
  await act(async () => {
    modeTrigger(locale).dispatchEvent(
      new MouseEvent("pointerdown", { bubbles: true }),
    );
    modeTrigger(locale).dispatchEvent(
      new MouseEvent("click", { bubbles: true }),
    );
    await new Promise((r) => setTimeout(r, 0));
  });
}

function menuItems(): HTMLElement[] {
  return Array.from(
    document.body.querySelectorAll<HTMLElement>("[role='menuitem']"),
  );
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("T25 thinking 开关对不支持模型的可见性", () => {
  it("不支持 thinking 的模型：菜单项置灰并提示可切换模型（中文）", async () => {
    mockModel(false);
    await renderInputBox("zh-CN");
    await openMenu("zh-CN");

    const items = menuItems();
    const thinking = items.find((el) =>
      el.textContent?.includes(zhCN.inputBox.reasoningMode),
    );
    expect(thinking).toBeTruthy();
    expect(thinking!.getAttribute("aria-disabled")).toBe("true");
    expect(thinking!.textContent).toContain(
      zhCN.inputBox.reasoningModeUnavailable,
    );
  });

  it("不支持 thinking 的模型：英文文案同样出现", async () => {
    mockModel(false);
    await renderInputBox("en-US");
    await openMenu("en-US");

    const items = menuItems();
    const thinking = items.find((el) =>
      el.textContent?.includes(enUS.inputBox.reasoningMode),
    );
    expect(thinking).toBeTruthy();
    expect(thinking!.getAttribute("aria-disabled")).toBe("true");
    expect(thinking!.textContent).toContain(
      enUS.inputBox.reasoningModeUnavailable,
    );
  });

  it("支持 thinking 的模型：菜单项可点、显示正常描述", async () => {
    mockModel(true);
    await renderInputBox("zh-CN");
    await openMenu("zh-CN");

    const items = menuItems();
    const thinking = items.find((el) =>
      el.textContent?.includes(zhCN.inputBox.reasoningMode),
    );
    expect(thinking).toBeTruthy();
    expect(thinking!.getAttribute("aria-disabled")).not.toBe("true");
    expect(thinking!.textContent).toContain(
      zhCN.inputBox.reasoningModeDescription,
    );
  });
});
