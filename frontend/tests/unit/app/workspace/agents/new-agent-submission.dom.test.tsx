/**
 * PR2（2026-11-24 前端可靠性批）：agents/new 的三个 sendMessage 调用点都观察
 * rejection，输入正文只在受理后清空。
 *
 * 按生产组件树渲染（页面自带 PromptInputProvider），useThreadStream 以 mock 替身
 * 直接产出 rejection；unhandledRejection 用监听器显式断言为空。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const h = vi.hoisted(() => ({
  sendMessage: vi.fn(),
  thread: {
    messages: [] as unknown[],
    values: {} as Record<string, unknown>,
    error: null as unknown,
    isLoading: false,
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/core/threads/hooks", () => ({
  useThreadStream: () => ({
    thread: h.thread,
    sendMessage: h.sendMessage,
    isHistoryLoading: false,
    hasMoreHistory: false,
    loadMoreHistory: vi.fn(),
  }),
}));

vi.mock("@/core/agents/api", () => ({
  AgentNameCheckError: class AgentNameCheckError extends Error {},
  checkAgentName: async () => ({ available: true }),
  createAgent: async () => ({ name: "e2e-agent" }),
  getAgent: async () => null,
}));

vi.mock("@/components/workspace/messages", () => ({
  MessageList: () => <div data-testid="message-list" />,
}));

vi.mock("@/components/workspace/artifacts", () => ({
  ArtifactsProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => children,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) =>
    children,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) =>
    children,
  DropdownMenuItem: (props: {
    onSelect?: (event: Event) => void;
    disabled?: boolean;
    children: React.ReactNode;
  }) => (
    <button
      type="button"
      data-testid="menu-save"
      disabled={props.disabled}
      onClick={(event) => props.onSelect?.(event.nativeEvent)}
    >
      {props.children}
    </button>
  ),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

import NewAgentPage from "@/app/workspace/agents/new/page";
import { I18nProvider } from "@/core/i18n/context";
import { SendRejectedError } from "@/core/threads/submission";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

// happy-dom 下 window.localStorage 不可用：页面读「保存提示已看过」标记，给一个 Map 桩
const storedHints = new Map<string, string>();
Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: {
    getItem: (key: string) => storedHints.get(key) ?? null,
    setItem: (key: string, value: string) => storedHints.set(key, value),
    removeItem: (key: string) => storedHints.delete(key),
    clear: () => storedHints.clear(),
  },
});

const toastError = vi.mocked(toast.error);
const toastSuccess = vi.mocked(toast.success);

let container: HTMLDivElement;
let root: Root;
let unhandled: unknown[];
const onUnhandled = (reason: unknown) => {
  unhandled.push(reason);
};

function setValue(
  element: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  act(() => {
    element.value = value;
    // React 的 value tracker 认为值没变时不触发 onChange：改值后重置它
    (
      element as HTMLTextAreaElement & {
        _valueTracker?: { setValue(v: string): void };
      }
    )._valueTracker?.setValue("");
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function settle(ms = 40) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

function chatTextarea(): HTMLTextAreaElement {
  const element = container.querySelector<HTMLTextAreaElement>("textarea");
  if (!element) throw new Error("chat textarea not rendered");
  return element;
}

/** 名称步 → 校验 → 创建 agent → 进入 chat 步（bootstrap 消息在此发出）。 */
async function reachChatStep() {
  const nameInput = container.querySelector<HTMLInputElement>("input");
  if (!nameInput) throw new Error("name input not rendered");
  setValue(nameInput, "e2e-agent");
  const continueButton =
    container.querySelector<HTMLButtonElement>("button.w-full");
  if (!continueButton) throw new Error("continue button not rendered");
  act(() => {
    continueButton.click();
  });
  await settle();
  expect(chatTextarea()).toBeTruthy();
}

function rejected(kind: "busy" | "stopped" | "failed") {
  // 故意不预先兜底：调用方若未观察，unhandledRejection 监听器会计数
  return Promise.reject(new SendRejectedError(kind, `${kind} for test`));
}

beforeEach(() => {
  storedHints.clear();
  unhandled = [];
  process.on("unhandledRejection", onUnhandled);
  h.sendMessage.mockReset();
  h.thread.isLoading = false;
  toastError.mockClear();
  toastSuccess.mockClear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  process.off("unhandledRejection", onUnhandled);
});

describe("agents/new 的三个调用点观察 rejection", () => {
  test("bootstrap 失败：进入 chat 步，无 unhandled rejection", async () => {
    h.sendMessage.mockImplementation(() => rejected("failed"));
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <NewAgentPage />
        </I18nProvider>,
      );
    });
    await reachChatStep();

    expect(h.sendMessage).toHaveBeenCalledTimes(1);
    expect(h.sendMessage.mock.calls[0]![1]).toMatchObject({ files: [] });
    expect(unhandled).toEqual([]);
  });

  test("普通聊天失败不清正文，无 unhandled rejection", async () => {
    h.sendMessage.mockImplementation(() => rejected("failed"));
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <NewAgentPage />
        </I18nProvider>,
      );
    });
    await reachChatStep();
    h.sendMessage.mockClear();

    setValue(chatTextarea(), "帮我写一首诗");
    await act(async () => {
      container
        .querySelector<HTMLButtonElement>("button[type='submit']")
        ?.click();
    });
    await settle();

    expect(h.sendMessage).toHaveBeenCalledTimes(1);
    expect(h.sendMessage.mock.calls[0]![1]).toMatchObject({
      text: "帮我写一首诗",
    });
    expect(chatTextarea().value).toBe("帮我写一首诗");
    expect(unhandled).toEqual([]);
  });

  test("busy 不清正文，页面不提示", async () => {
    h.sendMessage.mockImplementation(() => rejected("busy"));
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <NewAgentPage />
        </I18nProvider>,
      );
    });
    await reachChatStep();
    h.sendMessage.mockClear();

    setValue(chatTextarea(), "还在生成时又发一条");
    await act(async () => {
      container
        .querySelector<HTMLButtonElement>("button[type='submit']")
        ?.click();
    });
    await settle();

    expect(h.sendMessage).toHaveBeenCalledTimes(1);
    expect(chatTextarea().value).toBe("还在生成时又发一条");
    expect(toastError).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(unhandled).toEqual([]);
  });

  test("保存失败：不显示「已请求保存」，页面不重复 toast，状态恢复可再次保存", async () => {
    h.sendMessage.mockImplementation(() => rejected("failed"));
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <NewAgentPage />
        </I18nProvider>,
      );
    });
    await reachChatStep();
    h.sendMessage.mockClear();

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('[data-testid="menu-save"]')
        ?.click();
    });
    await settle();

    expect(h.sendMessage).toHaveBeenCalledTimes(1);
    expect(h.sendMessage.mock.calls[0]![3]).toMatchObject({
      additionalKwargs: { hide_from_ui: true },
    });
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
    // 状态已恢复：保存入口可再次触发
    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('[data-testid="menu-save"]')
        ?.click();
    });
    await settle();
    expect(h.sendMessage).toHaveBeenCalledTimes(2);
    expect(unhandled).toEqual([]);
  });
});
