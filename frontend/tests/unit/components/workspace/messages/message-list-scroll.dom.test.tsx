/**
 * PR3（2026-11-24 前端可靠性批）纵切 C：切线程整体重置滚动状态、首次水合落底。
 * 这里只断言状态重置（Conversation 以 threadId 为 key、initial="instant"）与
 * history loading 的可区分组合；滚动像素只能在 Playwright 里验。
 */

import type { Message } from "@langchain/langgraph-sdk";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  conversationMounts: 0,
  conversationProps: null as Record<string, unknown> | null,
}));

// Conversation 已整体替换：VirtualMessageList 需要的 StickToBottom 上下文由这里提供
vi.mock("use-stick-to-bottom", () => ({
  useStickToBottomContext: () => ({
    isAtBottom: true,
    scrollRef: { current: null },
    scrollToBottom: vi.fn(),
    stopScroll: vi.fn(),
  }),
}));

vi.mock("@/components/ai-elements/conversation", () => ({
  Conversation: (props: Record<string, unknown>) => {
    const { children, ...rest } = props;
    useEffect(() => {
      h.conversationMounts += 1;
    }, []);
    // 渲染期不改外部变量：由 effect 发布
    useEffect(() => {
      h.conversationProps = rest;
    });
    return <div data-testid="conversation">{children as React.ReactNode}</div>;
  },
  ConversationContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="conversation-content">{children}</div>
  ),
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { loading: "加载中", loadMore: "加载更多" },
      subtasks: { executing: (count: number) => `${count} 个子任务` },
    },
  }),
}));

vi.mock("@/components/workspace/messages/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <div data-testid="markdown-content">{content}</div>
  ),
}));

vi.mock("@/components/workspace/messages/message-group", () => ({
  MessageGroup: ({ messages }: { messages: Message[] }) => (
    <div data-testid="message-group">
      {messages.map((message) => message.id).join(",")}
    </div>
  ),
}));

vi.mock("@/components/workspace/messages/skeleton", () => ({
  MessageListSkeleton: () => <div data-testid="message-list-skeleton" />,
}));

vi.mock("@/components/workspace/messages/subtask-card", () => ({
  SubtaskCard: () => <div data-testid="subtask-card" />,
}));

vi.mock("@/components/workspace/messages/storyboard-confirm-card", () => ({
  StoryboardConfirmCard: () => <div data-testid="storyboard-card" />,
}));

vi.mock("@/components/workspace/copy-button", () => ({
  CopyButton: () => <button type="button">Copy</button>,
}));

vi.mock("@/core/tasks/subtask-status-sync", () => ({
  useSyncSubtaskStatus: () => undefined,
}));

import { MessageList } from "@/components/workspace/messages/message-list";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function makeThread(
  messages: Message[],
  { isThreadLoading = false, isLoading = false } = {},
) {
  return {
    messages,
    values: {},
    error: null,
    isLoading,
    isThreadLoading,
  } as unknown as Parameters<typeof MessageList>[0]["thread"];
}

const human = (id: string): Message =>
  ({ id, type: "human", content: `request ${id}` }) as Message;
const ai = (id: string): Message =>
  ({ id, type: "ai", content: `answer ${id}` }) as Message;

function render(element: React.ReactElement) {
  act(() => {
    root.render(element);
  });
}

beforeEach(() => {
  h.conversationMounts = 0;
  h.conversationProps = null;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("切线程整体重置", () => {
  it("Conversation 以 threadId 为 key 且 initial=instant：切线程整体重挂载，同线程重渲染不重挂载", () => {
    const first = makeThread([human("h1"), ai("a1")]);
    render(<MessageList threadId="t-1" thread={first} />);
    expect(h.conversationMounts).toBe(1);
    expect(h.conversationProps?.initial).toBe("instant");
    expect(container.textContent).toContain("request h1");
    expect(container.textContent).toContain("answer a1");

    // 同线程：消息变化只是重渲染，不重建 StickToBottom / 虚拟化状态
    render(
      <MessageList
        threadId="t-1"
        thread={makeThread([human("h1"), ai("a1"), ai("a2")])}
      />,
    );
    expect(h.conversationMounts).toBe(1);

    // 切线程：整个 Conversation 重挂载（滚动位置、StickToBottom、虚拟化 refs 归零）
    render(<MessageList threadId="t-2" thread={makeThread([human("h9")])} />);
    expect(h.conversationMounts).toBe(2);
    expect(container.textContent).toContain("request h9");
    expect(container.textContent).not.toContain("answer a2");
  });
});

describe("history loading 的可区分组合", () => {
  it("SDK 首屏还在加载且没有消息：显示骨架", () => {
    render(
      <MessageList
        threadId="t-1"
        thread={makeThread([], { isThreadLoading: true })}
      />,
    );
    expect(
      container.querySelector("[data-testid='message-list-skeleton']"),
    ).not.toBeNull();
  });

  it("已有消息、旧 run 还在加载：不显示骨架，Load more 入口显示加载中且不可点", () => {
    render(
      <MessageList
        threadId="t-1"
        thread={makeThread([human("h1"), ai("a1")])}
        hasMoreHistory
        isHistoryLoading
        loadMoreHistory={() => undefined}
      />,
    );
    expect(
      container.querySelector("[data-testid='message-list-skeleton']"),
    ).toBeNull();
    const button = container.querySelector<HTMLButtonElement>("button");
    expect(button?.textContent).toContain("加载中");
    expect(button?.disabled).toBe(true);
  });
});
