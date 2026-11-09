/**
 * P1-12：结构化澄清选项不可点击。
 *
 * 后端 `ask_clarification` 的 tool call args 里已经带了 `options`（writer 的
 * use-from-scratch.ts 早就在消费），但主聊天的 `message-group.tsx` 只渲染一行
 * 静态「需要你的协助」，没有任何按钮——用户只能照着文字自己重打一遍。
 *
 * 这里连真实输入框（PromptInput + PromptInputTextarea）一起渲染，断言点击选项
 * 走的是**同一条**提交链路：填进 name="message" 的 textarea → 提交同一个 form。
 * 受控（有 PromptInputProvider，主聊天页）与非受控（无 Provider，agents/new）
 * 两种挂载方式都要覆盖。
 *
 * 仓库无 @testing-library/react：沿用 subtask-card.test.tsx 的 createRoot +
 * act + happy-dom 手写 harness。
 */

import type { Message } from "@langchain/langgraph-sdk";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ code }: { code: string }) => <pre>{code}</pre>,
}));

vi.mock("@/components/workspace/messages/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <div>{content}</div>,
}));

vi.mock("@/components/workspace/artifacts", () => ({
  useArtifacts: () => ({
    setOpen: vi.fn(),
    autoOpen: false,
    autoSelect: false,
    selectedArtifact: null,
    select: vi.fn(),
  }),
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { thinking: "思考中" },
      toolCalls: {
        moreSteps: (count: number) => `查看其他 ${count} 个步骤`,
        lessSteps: "隐藏步骤",
        executeCommand: "执行命令",
        needYourHelp: "需要你的协助",
        useTool: (toolName: string) => `使用 “${toolName}” 工具`,
      },
    },
  }),
}));

import {
  PromptInput,
  PromptInputProvider,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input";
import { MessageGroup } from "@/components/workspace/messages/message-group";
import { getMessageGroups } from "@/core/messages/utils";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let caughtErrors: unknown[];

beforeEach(() => {
  caughtErrors = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container, {
    onCaughtError: (err) => caughtErrors.push(err),
    onUncaughtError: (err) => caughtErrors.push(err),
  });
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  document.body.innerHTML = "";
});

function clarificationMessages(args: Record<string, unknown>): Message[] {
  return [
    {
      type: "ai",
      id: "ai-1",
      content: "",
      tool_calls: [{ id: "tc-1", name: "ask_clarification", args }],
    } as unknown as Message,
  ];
}

function findButton(label: string) {
  return Array.from(container.querySelectorAll("button")).find(
    (button) => button.textContent?.trim() === label,
  );
}

/** 渲染「澄清步骤 + 真实输入框」，可选包一层 PromptInputProvider（受控形态）。 */
async function renderWithComposer({
  messages,
  isLoading = false,
  withProvider,
  onSubmit,
}: {
  messages: Message[];
  isLoading?: boolean;
  withProvider: boolean;
  onSubmit: (text: string) => void;
}) {
  const tree = (
    <>
      <MessageGroup messages={messages} isLoading={isLoading} />
      <PromptInput onSubmit={(message) => onSubmit(message.text)}>
        <PromptInputTextarea />
        <button type="submit">发送</button>
      </PromptInput>
    </>
  );
  await act(async () => {
    root.render(
      withProvider ? <PromptInputProvider>{tree}</PromptInputProvider> : tree,
    );
  });
}

describe("ask_clarification options", () => {
  test("renders one clickable button per option", async () => {
    await renderWithComposer({
      messages: clarificationMessages({
        question: "用哪种方式生成？",
        options: ["方案 A：先出大纲", "方案 B：直接成稿"],
      }),
      withProvider: true,
      onSubmit: vi.fn(),
    });

    expect(caughtErrors).toEqual([]);
    expect(container.textContent).toContain("需要你的协助");
    expect(findButton("方案 A：先出大纲")).toBeDefined();
    expect(findButton("方案 B：直接成稿")).toBeDefined();
  });

  test("clicking an option submits it through the composer form (controlled)", async () => {
    const onSubmit = vi.fn();
    await renderWithComposer({
      messages: clarificationMessages({
        question: "用哪种方式生成？",
        options: ["方案 A：先出大纲", "方案 B：直接成稿"],
      }),
      withProvider: true,
      onSubmit,
    });

    await act(async () => {
      findButton("方案 B：直接成稿")!.click();
    });
    // 提交沿用既有 followup 的「填框 → 下一个 tick 再 requestSubmit」时序
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(caughtErrors).toEqual([]);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("方案 B：直接成稿");
  });

  test("clicking an option submits it when the composer is uncontrolled", async () => {
    // agents/new 页面没有 PromptInputProvider，走 FormData 读值。
    const onSubmit = vi.fn();
    await renderWithComposer({
      messages: clarificationMessages({
        question: "用哪种方式生成？",
        options: ["方案 A：先出大纲"],
      }),
      withProvider: false,
      onSubmit,
    });

    await act(async () => {
      findButton("方案 A：先出大纲")!.click();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(caughtErrors).toEqual([]);
    expect(onSubmit).toHaveBeenCalledWith("方案 A：先出大纲");
  });

  test("accepts options serialized as a JSON string", async () => {
    // 部分模型（Qwen3-Max）把数组参数序列化成 JSON 字符串下发，
    // 后端 clarification_middleware 已对此做归一化，前端同样要容。
    await renderWithComposer({
      messages: clarificationMessages({
        question: "用哪种方式生成？",
        options: '["方案 A：先出大纲", "方案 B：直接成稿"]',
      }),
      withProvider: true,
      onSubmit: vi.fn(),
    });

    expect(caughtErrors).toEqual([]);
    expect(findButton("方案 A：先出大纲")).toBeDefined();
    expect(findButton("方案 B：直接成稿")).toBeDefined();
  });

  test("disables options while the thread is streaming", async () => {
    // 流式中输入框的提交会被 InputBox 转成「停止」，点选项等于掐掉本轮。
    await renderWithComposer({
      messages: clarificationMessages({
        question: "用哪种方式生成？",
        options: ["方案 A：先出大纲"],
      }),
      isLoading: true,
      withProvider: true,
      onSubmit: vi.fn(),
    });

    expect(caughtErrors).toEqual([]);
    expect(findButton("方案 A：先出大纲")?.disabled).toBe(true);
  });

  test("renders options for the real grouped payload (ai + tool result)", async () => {
    // 真实链路：getMessageGroups 把一次澄清拆成 assistant:processing[ai,tool] 与
    // assistant:clarification[tool] 两组，MessageGroup 收到的是**前者**——
    // 即 AI 消息后面还跟着 tool 结果。只喂 AI 消息的用例会漏掉这个形状。
    const onSubmit = vi.fn();
    const grouped = [
      ...clarificationMessages({
        question: "用哪种方式生成？",
        options: ["方案 A：先出大纲", "方案 B：直接成稿"],
      }),
      {
        type: "tool",
        id: "clarification:tc-1",
        name: "ask_clarification",
        tool_call_id: "tc-1",
        content:
          "❓ 用哪种方式生成？\n\n  1. 方案 A：先出大纲\n  2. 方案 B：直接成稿",
      } as unknown as Message,
    ];
    const groupTypes = getMessageGroups(grouped).map((group) => group.type);
    expect(groupTypes).toContain("assistant:processing");

    await renderWithComposer({
      messages: grouped,
      withProvider: true,
      onSubmit,
    });

    expect(caughtErrors).toEqual([]);
    expect(findButton("方案 A：先出大纲")).toBeDefined();

    await act(async () => {
      findButton("方案 A：先出大纲")!.click();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(onSubmit).toHaveBeenCalledWith("方案 A：先出大纲");
  });

  test("renders the plain step when the tool call carries no options", async () => {
    await renderWithComposer({
      messages: clarificationMessages({ question: "补一下文件路径？" }),
      withProvider: true,
      onSubmit: vi.fn(),
    });

    expect(caughtErrors).toEqual([]);
    expect(container.textContent).toContain("需要你的协助");
    // 输入框自带的「发送」按钮不算
    expect(
      Array.from(container.querySelectorAll("button")).map((b) =>
        b.textContent?.trim(),
      ),
    ).toEqual(["发送"]);
  });
});
