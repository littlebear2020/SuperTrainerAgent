/**
 * PR2（2026-11-24 前端可靠性批）：普通会话页的 auto-send 与手动发送都观察
 * sendMessage 的 rejection（accepted 以 onCreated 为准后，sendMessage 会 reject）。
 */

import { act, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const h = vi.hoisted(() => ({
  isNew: true,
  threadId: "thread-1",
  sendMessage: vi.fn(),
  inputProps: null as Record<string, unknown> | null,
}));

vi.mock("@/components/workspace/chats", () => ({
  ChatBox: ({ children }: { children: React.ReactNode }) => children,
  useSpecificChatMode: () => undefined,
  useThreadChat: () => ({
    threadId: h.threadId,
    setThreadId: vi.fn(),
    isNewThread: h.isNew,
    setIsNewThread: vi.fn(),
  }),
}));

vi.mock("@/core/threads/hooks", () => ({
  useThreadStream: () => ({
    thread: { messages: [], values: {}, error: null, isLoading: false },
    sendMessage: h.sendMessage,
    stop: vi.fn(),
    isUploading: false,
    uploadProgress: 0,
    isHistoryLoading: false,
    hasMoreHistory: false,
    loadMoreHistory: vi.fn(),
  }),
}));

vi.mock("@/core/settings", () => ({
  useThreadSettings: () => [{ context: {} }, vi.fn()],
}));

vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({ tokenUsageEnabled: false, tokenBudgetWarnTokens: {} }),
}));

vi.mock("@/core/threads/token-usage", () => ({
  useThreadTokenUsage: () => null,
}));

vi.mock("@/core/notification/hooks", () => ({
  useNotification: () => ({ showNotification: vi.fn() }),
}));

vi.mock("@/env", () => ({
  env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" },
}));

vi.mock("@/components/workspace/messages", () => ({
  MessageList: () => <div data-testid="message-list" />,
}));

vi.mock("@/components/workspace/input-box", () => ({
  InputBox: (props: Record<string, unknown>) => {
    h.inputProps = props;
    return <div data-testid="input-box" />;
  },
}));

vi.mock("@/components/workspace/thread-title", () => ({
  ThreadTitle: () => null,
}));
vi.mock("@/components/workspace/token-usage-indicator", () => ({
  TokenUsageIndicator: () => null,
}));
vi.mock("@/components/workspace/export-trigger", () => ({
  ExportTrigger: () => null,
}));
vi.mock("@/components/workspace/artifacts", () => ({
  ArtifactTrigger: () => null,
}));
vi.mock("@/components/workspace/todo-list", () => ({
  TodoList: () => null,
}));
vi.mock("@/components/workspace/welcome", () => ({
  Welcome: () => null,
}));

import ChatPage from "@/app/workspace/chats/[thread_id]/page";
import {
  PromptInputProvider,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";
import { I18nProvider } from "@/core/i18n/context";
import { SendRejectedError } from "@/core/threads/submission";
import { PENDING_PROMPT_KEY } from "@/lib/pending-prompt";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let unhandled: unknown[];
const onUnhandled = (reason: unknown) => {
  unhandled.push(reason);
};

function PromptValue() {
  const { textInput } = usePromptInputController();
  return <output data-testid="prompt-value">{textInput.value}</output>;
}

function promptValue(): string {
  return (
    document.querySelector("[data-testid='prompt-value']")?.textContent ?? ""
  );
}

/** InputBox 在 mountedRef 置真后的下一次渲染才挂载；全部依赖都是静态替身，
 * 所以由外壳在挂载后再渲染一次（生产里由查询 / 路由更新触发）。 */
function Harness() {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(() => setTick((n) => n + 1), 5);
    return () => window.clearTimeout(timer);
  }, []);
  return <ChatPage />;
}

async function renderPage() {
  document.cookie = "locale=zh-CN; path=/";
  await act(async () => {
    root.render(
      <I18nProvider initialLocale="zh-CN">
        <PromptInputProvider>
          <Harness />
          <PromptValue />
        </PromptInputProvider>
      </I18nProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 40));
  });
  expect(h.inputProps, "InputBox not mounted").not.toBeNull();
}

beforeEach(() => {
  unhandled = [];
  process.on("unhandledRejection", onUnhandled);
  h.isNew = true;
  h.threadId = "thread-1";
  h.inputProps = null;
  h.sendMessage.mockReset();
  window.sessionStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  window.sessionStorage.clear();
  process.off("unhandledRejection", onUnhandled);
});

describe("普通会话页观察 sendMessage 的 rejection", () => {
  test("auto-send 失败：只发一次，无 unhandled rejection", async () => {
    h.sendMessage.mockImplementation(() =>
      Promise.reject(new SendRejectedError("failed", "create failed")),
    );
    window.sessionStorage.setItem(
      PENDING_PROMPT_KEY,
      JSON.stringify({ prompt: "来自灵感库的长提示词", autoSend: true }),
    );

    await renderPage();

    expect(h.sendMessage).toHaveBeenCalledTimes(1);
    expect(h.sendMessage.mock.calls[0]![1]).toMatchObject({
      text: "来自灵感库的长提示词",
      files: [],
    });
    expect(unhandled).toEqual([]);
  });

  test.each(["busy", "failed"] as const)(
    "auto-send 被 %s 拒绝：提示词回到输入框",
    async (kind) => {
      h.sendMessage.mockImplementation(() =>
        Promise.reject(new SendRejectedError(kind, kind)),
      );
      window.sessionStorage.setItem(
        PENDING_PROMPT_KEY,
        JSON.stringify({ prompt: "被拒绝的提示词", autoSend: true }),
      );

      await renderPage();
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });

      expect(promptValue()).toBe("被拒绝的提示词");
      expect(unhandled).toEqual([]);
    },
  );

  test("auto-send 被 stopped 拒绝（切线程触发）时不在新线程回填提示词", async () => {
    h.sendMessage.mockImplementation(() =>
      Promise.reject(new SendRejectedError("stopped", "stopped")),
    );
    window.sessionStorage.setItem(
      PENDING_PROMPT_KEY,
      JSON.stringify({ prompt: "切换线程时被停止的提示词", autoSend: true }),
    );

    await renderPage();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(h.sendMessage).toHaveBeenCalledTimes(1);
    expect(promptValue()).toBe("");
    expect(unhandled).toEqual([]);
  });

  test("手动发送把 sendMessage 的 Promise 原样交给 InputBox", async () => {
    const sent = Promise.reject(new SendRejectedError("busy", "busy"));
    // 页面原样返回同一个 Promise 对象；这里只验证身份，预先兜底避免本用例自己制造 unhandled
    void sent.catch(() => undefined);
    h.sendMessage.mockImplementation(() => sent);

    await renderPage();
    const onSubmit = h.inputProps?.onSubmit as
      | ((message: { text: string; files: [] }) => unknown)
      | undefined;
    expect(typeof onSubmit).toBe("function");

    const returned = onSubmit!({ text: "hi", files: [] });
    expect(returned).toBe(sent);
    expect(unhandled).toEqual([]);
  });
});
