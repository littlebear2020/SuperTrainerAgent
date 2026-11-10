import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  agentName: "course-video",
  search: "",
  isNew: true,
  threadId: "new",
  agentLoading: false,
  agentError: null as Error | null,
  staticDemo: false,
  refetch: vi.fn(),
  sendMessage: vi.fn(),
  guideProps: null as Record<string, unknown> | null,
  inputProps: null as Record<string, unknown> | null,
  guideHistory: [] as Record<string, unknown>[],
  inputMounts: 0,
  inputUnmounts: 0,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ agent_name: h.agentName }),
  useSearchParams: () => new URLSearchParams(h.search),
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/core/agents", async () => {
  const actual =
    await vi.importActual<Record<string, unknown>>("@/core/agents");
  return {
    ...actual,
    useAgent: () => ({
      agent: {
        name: h.agentName,
        description: "Agent description",
        skills: [],
      },
      isLoading: h.agentLoading,
      error: h.agentError,
      refetch: h.refetch,
    }),
  };
});

vi.mock("@/core/threads/hooks", () => ({
  useThreadStream: () => ({
    thread: {
      messages: [],
      values: {},
      error: null,
      isLoading: false,
    },
    sendMessage: h.sendMessage,
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
  get env() {
    return {
      NEXT_PUBLIC_STATIC_WEBSITE_ONLY: h.staticDemo ? "true" : "false",
    };
  },
}));

vi.mock("@/components/workspace/chats", () => ({
  ChatBox: ({ children }: { children: React.ReactNode }) => children,
  useThreadChat: () => ({
    threadId: h.threadId,
    setThreadId: vi.fn(),
    isNewThread: h.isNew,
    setIsNewThread: vi.fn(),
  }),
}));

vi.mock("@/components/workspace/messages", () => ({
  MessageList: () => <div data-testid="message-list" />,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM: 0,
  MESSAGE_LIST_FOLLOWUPS_EXTRA_PADDING_BOTTOM: 0,
}));

vi.mock("@/components/workspace/agent-welcome", () => ({
  AgentWelcome: () => <div data-testid="legacy-agent-welcome" />,
}));

vi.mock("@/components/workspace/agent-new-chat-guide", () => ({
  AgentNewChatGuide: (props: Record<string, unknown>) => {
    h.guideProps = props;
    h.guideHistory.push(props);
    return (
      <div data-testid="agent-new-chat-guide">
        {props.children as React.ReactNode}
      </div>
    );
  },
}));

vi.mock("@/components/workspace/input-box", async () => {
  const { useEffect } = await import("react");
  return {
    InputBox: (props: Record<string, unknown>) => {
      h.inputProps = props;
      useEffect(() => {
        h.inputMounts += 1;
        return () => {
          h.inputUnmounts += 1;
        };
      }, []);
      return (
        <div data-testid="input-box">
          {props.extraHeader as React.ReactNode}
        </div>
      );
    },
  };
});

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
vi.mock("@/components/workspace/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => children,
}));

import AgentChatPage from "@/app/workspace/agents/[agent_name]/chats/[thread_id]/page";
import {
  PromptInputProvider,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function PromptValue() {
  const { textInput } = usePromptInputController();
  return <output data-testid="prompt-value">{textInput.value}</output>;
}

describe("AgentChatPage guide wiring", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    document.cookie = "locale=zh-CN; path=/";
    h.agentName = "course-video";
    h.search = "";
    h.isNew = true;
    h.threadId = "new";
    h.agentLoading = false;
    h.agentError = null;
    h.staticDemo = false;
    h.refetch.mockClear();
    h.guideProps = null;
    h.inputProps = null;
    h.guideHistory = [];
    h.inputMounts = 0;
    h.inputUnmounts = 0;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderPage() {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <PromptInputProvider>
            <AgentChatPage />
            <PromptValue />
          </PromptInputProvider>
        </I18nProvider>,
      );
    });
  }

  it("uses the guided layout, readable badge and safe input props", async () => {
    await renderPage();

    expect(container.querySelector("header")?.textContent).toContain(
      "课程视频",
    );
    expect(container.querySelector("header")?.textContent).not.toContain(
      "course-video",
    );
    expect(h.guideProps).toMatchObject({
      agentName: "course-video",
      status: "ready",
    });
    expect(h.inputProps).toMatchObject({
      showNewThreadSuggestions: false,
      autoFocus: false,
    });
  });

  it("keeps existing threads and non-target agents on their current branches", async () => {
    h.isNew = false;
    h.threadId = "thread-1";
    await renderPage();
    expect(
      container.querySelector("[data-testid='agent-new-chat-guide']"),
    ).toBeNull();
    expect(
      container.querySelector("[data-testid='message-list']"),
    ).not.toBeNull();

    h.isNew = true;
    h.threadId = "new";
    h.agentName = "test-agent";
    await renderPage();
    expect(
      container.querySelector("[data-testid='legacy-agent-welcome']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='agent-new-chat-guide']"),
    ).toBeNull();
  });

  it("passes loading/error states and retries through the existing query", async () => {
    h.agentLoading = true;
    await renderPage();
    expect(h.guideProps).toMatchObject({ status: "loading" });
    expect(h.inputProps).toMatchObject({ disabled: true });

    h.agentLoading = false;
    h.agentError = new Error("failed");
    await renderPage();
    expect(h.guideProps).toMatchObject({ status: "error" });
    const onRetry = h.guideProps?.onRetry;
    expect(typeof onRetry).toBe("function");
    await act(async () => (onRetry as () => void)());
    expect(h.refetch).toHaveBeenCalledTimes(1);
  });

  it("gates cases on the completed handoff key without remounting InputBox", async () => {
    h.search = "msg=first";
    await renderPage();
    expect(h.guideHistory[0]?.handoffReady).toBe(false);
    expect(h.guideProps?.handoffReady).toBe(true);
    expect(
      container.querySelector("[data-testid='prompt-value']")?.textContent,
    ).toBe("first");

    h.search = "msg=second";
    await renderPage();
    expect(h.guideHistory.some((props) => props.handoffReady === false)).toBe(
      true,
    );
    expect(h.guideProps?.handoffReady).toBe(true);
    expect(
      container.querySelector("[data-testid='prompt-value']")?.textContent,
    ).toBe("second");
    expect(h.inputMounts).toBe(1);
    expect(h.inputUnmounts).toBe(0);
  });

  it("keeps static demo mode disabled without restoring autoFocus", async () => {
    h.staticDemo = true;
    await renderPage();

    expect(h.inputProps).toMatchObject({ disabled: true, autoFocus: false });
  });
});
