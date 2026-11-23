import type { Message } from "@langchain/langgraph-sdk";
import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MessageList } from "@/components/workspace/messages/message-list";
import type * as VirtualMessageListModule from "@/components/workspace/messages/virtual-message-list";
import type * as MessagesUsageModule from "@/core/messages/usage";

const probe = vi.hoisted(() => ({
  emptyTurnUsage: Object.freeze(new Map()),
  renderAfterGroup: undefined as ((index: number) => ReactNode) | undefined,
  summarizeTurnUsage: vi.fn(),
}));

vi.mock("@/core/messages/usage", async (importOriginal) => {
  const actual = await importOriginal<typeof MessagesUsageModule>();
  return {
    ...actual,
    EMPTY_TURN_USAGE: probe.emptyTurnUsage,
    summarizeTurnUsage: probe.summarizeTurnUsage,
  };
});

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      clipboard: { copyToClipboard: "Copy", copied: "Copied" },
      common: { loading: "Loading", loadMore: "Load more" },
      errorFallback: { title: "Error", retry: "Retry" },
      messages: { share: "Share", feedback: "Feedback" },
      subtasks: { executing: (count: number) => `${count} subtasks` },
      tokenUsage: {
        label: "Tokens",
        input: "Input",
        output: "Output",
        total: "Total",
        unavailableShort: "No usage returned",
      },
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

vi.mock("@/components/workspace/artifacts/artifact-file-list", () => ({
  ArtifactFileList: ({ files }: { files: string[] }) => (
    <div data-testid="artifact-files">{files.join(",")}</div>
  ),
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

vi.mock(
  "@/components/workspace/messages/virtual-message-list",
  async (importOriginal) => {
    const actual = await importOriginal<typeof VirtualMessageListModule>();
    return {
      ...actual,
      VirtualMessageList: (
        props: ComponentProps<typeof actual.VirtualMessageList>,
      ) => {
        probe.renderAfterGroup = props.renderAfterGroup;
        return <actual.VirtualMessageList {...props} />;
      },
    };
  },
);

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

type Thread = ComponentProps<typeof MessageList>["thread"];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  probe.summarizeTurnUsage.mockReset();
  probe.renderAfterGroup = undefined;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function human(id: string): Message {
  return { id, type: "human", content: `request ${id}` } as Message;
}

function ai(
  id: string,
  content: string,
  inputTokens: number,
  outputTokens: number,
  totalTokens: number,
): Message {
  return {
    id,
    type: "ai",
    content,
    usage_metadata: {
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      total_tokens: totalTokens,
    },
  } as Message;
}

function toolAi(
  id: string,
  toolCallId: string,
  name: string,
  inputTokens: number,
  outputTokens: number,
  totalTokens: number,
): Message {
  return {
    id,
    type: "ai",
    content: "",
    tool_calls: [
      {
        id: toolCallId,
        name,
        args:
          name === "present_files"
            ? { filepaths: ["/mnt/data/result.txt"] }
            : { command: "true" },
      },
    ],
    usage_metadata: {
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      total_tokens: totalTokens,
    },
  } as Message;
}

function toolResult(
  id: string,
  toolCallId: string,
  name = "bash",
  content = '{"status":"done"}',
): Message {
  return {
    id,
    type: "tool",
    name,
    tool_call_id: toolCallId,
    content,
  } as Message;
}

function makeThread(messages: Message[], isLoading = false): Thread {
  return { messages, isLoading, isThreadLoading: false } as unknown as Thread;
}

function renderMessageList(
  thread: Thread,
  tokenUsageEnabled = true,
  paddingBottom = 160,
) {
  act(() => {
    root.render(
      <MessageList
        threadId="thread-1"
        thread={thread}
        tokenUsageEnabled={tokenUsageEnabled}
        paddingBottom={paddingBottom}
      />,
    );
  });
}

function turnRows() {
  return container.querySelectorAll('[data-testid="turn-token-usage"]');
}

function inputRowCount() {
  return container.textContent?.match(/Input:/g)?.length ?? 0;
}

describe("MessageList turn token usage", () => {
  it("renders one summary after the final group across processing, files, and answer", () => {
    const thread = makeThread([
      human("h-1"),
      toolAi("processing", "call-1", "bash", 1, 2, 3),
      toolResult("tool-1", "call-1"),
      toolAi("present", "call-2", "present_files", 10, 20, 30),
      ai("final", "answer", 100, 200, 300),
    ]);
    probe.summarizeTurnUsage.mockReturnValue(
      new Map([
        [
          3,
          {
            usage: { inputTokens: 111, outputTokens: 222, totalTokens: 333 },
            missingCount: 0,
          },
        ],
      ]),
    );

    renderMessageList(thread);

    expect(inputRowCount()).toBe(1);
    expect(turnRows()).toHaveLength(1);
    const finalRow = container.querySelector('[data-message-group-index="3"]');
    expect(finalRow?.contains(turnRows()[0]!)).toBe(true);
    expect(finalRow?.lastElementChild).toBe(turnRows()[0]);
    expect(turnRows()[0]?.textContent).toContain("Input: 111");
    expect(turnRows()[0]?.textContent).toContain("Output: 222");
    expect(turnRows()[0]?.textContent).toContain("Total: 333");
  });

  it("renders one independently valued summary for each human turn", () => {
    const thread = makeThread([
      human("h-1"),
      ai("a-1", "first", 1, 2, 3),
      human("h-2"),
      ai("a-2", "second", 10, 20, 30),
    ]);
    probe.summarizeTurnUsage.mockReturnValue(
      new Map([
        [
          1,
          {
            usage: { inputTokens: 1, outputTokens: 2, totalTokens: 3 },
            missingCount: 0,
          },
        ],
        [
          3,
          {
            usage: { inputTokens: 10, outputTokens: 20, totalTokens: 30 },
            missingCount: 0,
          },
        ],
      ]),
    );

    renderMessageList(thread);

    expect(inputRowCount()).toBe(2);
    expect(turnRows()).toHaveLength(2);
    expect(
      container
        .querySelector('[data-message-group-index="1"]')
        ?.contains(turnRows()[0]!),
    ).toBe(true);
    expect(
      container
        .querySelector('[data-message-group-index="3"]')
        ?.contains(turnRows()[1]!),
    ).toBe(true);
    expect(turnRows()[0]?.textContent).toContain("Total: 3");
    expect(turnRows()[1]?.textContent).toContain("Total: 30");
  });

  it.each([
    { label: "the feature switch is off", enabled: false, isLoading: false },
    { label: "the thread is loading", enabled: true, isLoading: true },
  ])(
    "does no summarization and reuses the empty callback when $label",
    ({ enabled, isLoading }) => {
      const thread = makeThread(
        [human("h-1"), ai("a-1", "answer", 1, 2, 3)],
        isLoading,
      );

      renderMessageList(thread, enabled);
      const firstAfterGroup = probe.renderAfterGroup;

      expect(probe.summarizeTurnUsage).not.toHaveBeenCalled();
      expect(inputRowCount()).toBe(0);
      expect(turnRows()).toHaveLength(0);
      expect(firstAfterGroup).toBeDefined();
      expect(firstAfterGroup?.(0)).toBeNull();
      expect(firstAfterGroup?.(1)).toBeNull();

      renderMessageList(thread, enabled, 200);
      expect(probe.summarizeTurnUsage).not.toHaveBeenCalled();
      expect(probe.renderAfterGroup).toBe(firstAfterGroup);
    },
  );

  it("keeps the summary on the last renderable group before an empty clarification", () => {
    const thread = makeThread([
      human("h-1"),
      ai("final", "answer", 10, 20, 30),
      toolAi("ask", "clarification-call", "ask_clarification", 1, 2, 3),
      toolResult(
        "clarification-result",
        "clarification-call",
        "ask_clarification",
        "",
      ),
    ]);
    probe.summarizeTurnUsage.mockReturnValue(
      new Map([
        [
          2,
          {
            usage: { inputTokens: 11, outputTokens: 22, totalTokens: 33 },
            missingCount: 0,
          },
        ],
      ]),
    );

    renderMessageList(thread);

    expect(inputRowCount()).toBe(1);
    expect(turnRows()).toHaveLength(1);
    expect(
      container
        .querySelector('[data-message-group-index="2"]')
        ?.contains(turnRows()[0]!),
    ).toBe(true);
    expect(
      container.querySelector('[data-message-group-index="3"]'),
    ).toBeNull();
  });
});
