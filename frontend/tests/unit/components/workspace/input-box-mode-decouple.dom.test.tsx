/**
 * 缺陷 1 解耦 + 缺陷 2 三路径（2026-08-27 计划，复用 T25 测试的 mock 骨架）：
 *
 * 1. 缺陷 1：supports_thinking=false 的模型，菜单点 Pro/Ultra → onContextChange
 *    收到 mode: "pro"/"ultra"（不弹回 flash）。
 * 2. 路径 B：context.model_name 为已撤册模型 → 回退 models[0]、delisted toast
 *    出现一次、原 mode 为 pro 时保持 pro。
 * 3. 路径 A：useModels 返回 error → 带重试 action 的 toast.error；点 action
 *    调用 refetch；重复渲染不刷屏。
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

describe("缺陷 1：supports_thinking=false 模型上 Pro/Ultra 不再弹回", () => {
  it("点 Pro → onContextChange 收到 mode: pro + reasoning_effort: medium", async () => {
    mockUseModels();
    const onContextChange = vi.fn();
    await renderInputBox({
      context: { model_name: "m1", mode: "flash" },
      onContextChange,
    });
    await openMenu();

    const pro = menuItems().find((el) =>
      el.textContent?.includes(enUS.inputBox.proModeDescription),
    );
    expect(pro).toBeTruthy();
    await clickMenuItem(pro!);

    expect(onContextChange).toHaveBeenCalled();
    expect(onContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        model_name: "m1",
        mode: "pro",
        reasoning_effort: "medium",
      }),
    );
  });

  it("点 Ultra → onContextChange 收到 mode: ultra + reasoning_effort: high", async () => {
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
        reasoning_effort: "high",
      }),
    );
  });
});

describe("缺陷 2 路径 B：已撤册模型回退默认并提示", () => {
  it("context.model_name 不在列表 → 回退 models[0]、toast.info 一次、mode pro 保持", async () => {
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
      context: { model_name: "delisted-x", mode: "pro" },
      onContextChange,
    });

    // 回退默认模型；mode pro 在支持 thinking 的默认模型上保持 pro（不被改写 flash）
    expect(onContextChange).toHaveBeenCalledWith(
      expect.objectContaining({ model_name: "m1", mode: "pro" }),
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
