/**
 * 模式解耦与模型列表错误路径：
 *
 * 1. supports_thinking=false 的模型仍可选标准/Ultra，且不持久化独立 effort。
 * 2. 已撤册模型回退到合法默认，standard 保持，delisted toast 只提示一次。
 * 3. useModels 返回 error 时显示可重试 toast，重复渲染不刷屏。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
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
import { enUS } from "@/core/i18n/locales";
import { useModels } from "@/core/models/hooks";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const fakeThread = { messages: [] } as never;

let container: HTMLDivElement;
let root: Root;

function mockUseModels(overrides: Record<string, unknown> = {}) {
  (useModels as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    models: [
      {
        name: "m1",
        display_name: "模型一",
        model: "m1",
        supports_thinking: false,
      },
    ],
    tokenUsageEnabled: false,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  });
}

type InputBoxProps = Parameters<typeof InputBox>[0];
type ContextProp = InputBoxProps["context"];
type OnContextChangeProp = InputBoxProps["onContextChange"];

async function renderInputBox({
  context,
  onContextChange,
}: {
  context: ContextProp;
  onContextChange: ReturnType<typeof vi.fn>;
}) {
  await act(async () => root.unmount());
  root = createRoot(container);
  document.cookie = "locale=en-US; path=/";
  await act(async () => {
    root.render(
      <I18nProvider initialLocale="en-US">
        <ThreadContext.Provider value={{ thread: fakeThread }}>
          <PromptInputProvider initialInput="">
            <InputBox
              threadId="t1"
              context={context}
              // vi.fn() 的 Mock 类型与 props 签名不兼容（索引签名拓宽），此处收窄。
              onContextChange={
                onContextChange as unknown as OnContextChangeProp
              }
            />
          </PromptInputProvider>
        </ThreadContext.Provider>
      </I18nProvider>,
    );
  });
}

function modeTrigger(): HTMLButtonElement {
  const buttons = Array.from(
    document.querySelectorAll<HTMLButtonElement>("button"),
  );
  const trigger = buttons.find((b) =>
    b.textContent?.includes(enUS.inputBox.flashMode),
  );
  if (!trigger) throw new Error("mode trigger not rendered");
  return trigger;
}

async function openMenu() {
  await act(async () => {
    modeTrigger().dispatchEvent(
      new MouseEvent("pointerdown", { bubbles: true }),
    );
    modeTrigger().dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 0));
  });
}

function menuItems(): HTMLElement[] {
  return Array.from(
    document.body.querySelectorAll<HTMLElement>("[role='menuitem']"),
  );
}

async function clickMenuItem(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 0));
  });
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

describe("supports_thinking=false 模型仍可选标准/Ultra", () => {
  it("点标准 → onContextChange 收到 mode: standard", async () => {
    mockUseModels();
    const onContextChange = vi.fn();
    await renderInputBox({
      context: { model_name: "m1", mode: "flash" },
      onContextChange,
    });
    await openMenu();

    const standard = menuItems().find((el) =>
      el.textContent?.includes(enUS.inputBox.standardModeDescription),
    );
    expect(standard).toBeTruthy();
    await clickMenuItem(standard!);

    expect(onContextChange).toHaveBeenCalled();
    expect(onContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        model_name: "m1",
        mode: "standard",
      }),
    );
  });

  it("点 Ultra → onContextChange 收到 mode: ultra", async () => {
    mockUseModels();
    const onContextChange = vi.fn();
    await renderInputBox({
      context: { model_name: "m1", mode: "flash" },
      onContextChange,
    });
    await openMenu();

    const ultra = menuItems().find((el) =>
      el.textContent?.includes(enUS.inputBox.ultraModeDescription),
    );
    expect(ultra).toBeTruthy();
    await clickMenuItem(ultra!);

    expect(onContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        model_name: "m1",
        mode: "ultra",
      }),
    );
  });
});

describe("缺陷 2 路径 B：已撤册模型回退默认并提示", () => {
  it("context.model_name 不在列表 → 回退合法默认、toast.info 一次、standard 保持", async () => {
    mockUseModels({
      models: [
        {
          name: "m1",
          display_name: "模型一",
          model: "m1",
          supports_thinking: true,
        },
      ],
    });
    const onContextChange = vi.fn();
    await renderInputBox({
      context: { model_name: "delisted-x", mode: "standard" },
      onContextChange,
    });

    // 回退合法默认模型；standard 不受模型 thinking 能力影响。
    expect(onContextChange).toHaveBeenCalledWith(
      expect.objectContaining({ model_name: "m1", mode: "standard" }),
    );

    // delisted toast 恰好一次，含被撤册模型名
    expect(toast.info).toHaveBeenCalledTimes(1);
    expect(toast.info).toHaveBeenCalledWith(
      expect.stringContaining("delisted-x"),
    );

    // 重复渲染不刷屏
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(toast.info).toHaveBeenCalledTimes(1);
  });
});

describe("缺陷 2 路径 A：模型列表加载失败可见且可重试", () => {
  it("error → 带重试 action 的 toast.error；点 action 调 refetch；重渲染不刷屏", async () => {
    const refetch = vi.fn();
    mockUseModels({ models: [], error: new Error("boom"), refetch });
    const onContextChange = vi.fn();
    await renderInputBox({
      context: { model_name: "m1", mode: "flash" },
      onContextChange,
    });

    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalledWith(
      enUS.inputBox.modelsLoadFailed,
      expect.objectContaining({
        action: expect.objectContaining({
          label: enUS.inputBox.retry,
        }),
      }),
    );

    // 模型列表为空 → 不触发自动纠正回写（不污染存储）
    expect(onContextChange).not.toHaveBeenCalled();

    // 同一 error 重复渲染不刷屏
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(toast.error).toHaveBeenCalledTimes(1);

    // 点重试 → refetch
    const call = (toast.error as ReturnType<typeof vi.fn>).mock
      .calls[0]![1] as { action: { onClick: () => void } };
    await act(async () => {
      call.action.onClick();
    });
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});

describe("P5 动态默认模型徽标", () => {
  const models = [
    {
      name: "deepseek-v4",
      display_name: "DeepSeek 默认版（默认）",
      model: "deepseek-v4",
    },
    {
      name: "claude-sonnet-5-5",
      display_name: "Claude Sonnet (默认)",
      model: "claude-sonnet-5-5",
    },
  ];

  function modelItems(): HTMLElement[] {
    return Array.from(document.body.querySelectorAll("[cmdk-item]"));
  }

  it("选中按钮和当前档列表只给 mode_defaults 指定模型画默认徽标", async () => {
    mockUseModels({
      models,
      modeDefaults: {
        flash: "deepseek-v4",
        standard: "claude-sonnet-5-5",
        ultra: "deepseek-v4",
      },
    });
    await renderInputBox({
      context: { model_name: "claude-sonnet-5-5", mode: "standard" },
      onContextChange: vi.fn(),
    });

    const trigger = Array.from(
      document.querySelectorAll<HTMLButtonElement>("button"),
    ).find((button) => button.textContent?.includes("Claude Sonnet"));
    expect(trigger).toBeDefined();
    expect(trigger!.textContent).toContain("默认");
    expect(trigger!.textContent).not.toContain("(默认)");

    await act(async () => {
      trigger!.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
      trigger!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const items = modelItems();
    expect(items).toHaveLength(2);
    const deepseek = items.find((item) =>
      item.textContent?.includes("DeepSeek"),
    );
    const claude = items.find((item) =>
      item.textContent?.includes("Claude Sonnet"),
    );
    expect(deepseek!.textContent).toContain("DeepSeek 默认版");
    expect(deepseek!.textContent).not.toContain("（默认）");
    expect(
      deepseek!.querySelector("[data-testid='default-model-badge']"),
    ).toBeNull();
    expect(claude!.textContent).toContain("Claude Sonnet");
    expect(claude!.textContent).not.toContain("(默认)");
    expect(
      claude!.querySelectorAll("[data-testid='default-model-badge']"),
    ).toHaveLength(1);
  });

  it("当前模型不是本档默认时按钮不画默认徽标，但仍剥离末尾后缀", async () => {
    mockUseModels({
      models,
      modeDefaults: { standard: "claude-sonnet-5-5" },
    });
    await renderInputBox({
      context: { model_name: "deepseek-v4", mode: "standard" },
      onContextChange: vi.fn(),
    });
    const trigger = Array.from(
      document.querySelectorAll<HTMLButtonElement>("button"),
    ).find((button) => button.textContent?.includes("DeepSeek"));
    expect(trigger).toBeDefined();
    expect(trigger!.textContent).toContain("DeepSeek 默认版");
    expect(trigger!.textContent).not.toContain("（默认）");
    expect(
      trigger!.querySelector("[data-testid='default-model-badge']"),
    ).toBeNull();
  });
});

describe("P13 composer 稳定定位", () => {
  it("输入框提供与本地化占位文案无关的稳定 testid", async () => {
    mockUseModels();
    await renderInputBox({
      context: { model_name: "m1", mode: "standard" },
      onContextChange: vi.fn(),
    });
    const input = document.querySelector("[data-testid='composer-input']");
    expect(input).not.toBeNull();
    expect(input!.tagName).toBe("TEXTAREA");
    expect((input as HTMLTextAreaElement).disabled).toBe(false);
  });
});
