/**
 * P1-5 下半（组件层证明）：`MessageGroup` 未 memo 化 → 父组件每次重渲染都
 * 连带重渲染每个消息组的整棵子树。
 *
 * 「测试变绿」不足以证明重渲染真的减少了，所以这里**数渲染次数**：父组件
 * 改一个与消息无关的 state（真实场景：gallery 设置异步落地、token 开关、
 * 流式指示器跳动），断言 messages 引用不变时 MessageGroup 的子树一次都不重
 * 渲染。未 memo 时这个断言必然失败。
 *
 * 计数点选在 MessageGroup **内部**渲染的叶子（被 mock 的 CodeBlock）：外面包
 * 一层计数组件是测不出来的——未 memo 的包装层自己总会重渲染，数到的是包装层
 * 而不是 MessageGroup。叶子被重新调用，才证明 MessageGroup 的渲染输出被重算。
 *
 * 仓库无 @testing-library/react：沿用 subtask-card.test.tsx 的 createRoot +
 * act + happy-dom 手写 harness。
 */

import type { Message } from "@langchain/langgraph-sdk";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// 渲染计数器：mock 工厂会被提升到 import 之前，计数器必须用 vi.hoisted。
const probe = vi.hoisted(() => ({ codeBlockRenders: 0 }));

vi.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ code }: { code: string }) => {
    probe.codeBlockRenders++;
    return <pre>{code}</pre>;
  },
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

import { MessageGroup } from "@/components/workspace/messages/message-group";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  probe.codeBlockRenders = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  act(() => {
    root = createRoot(container);
  });
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

function toolCallMessage(id: string, command = "echo hi"): Message {
  return {
    type: "ai",
    id,
    content: "",
    tool_calls: [{ id: `${id}-call`, name: "bash", args: { command } }],
  } as Message;
}

test("messages 引用不变时，父组件重渲染不应重渲染 MessageGroup 子树", () => {
  const messages = [toolCallMessage("a1")];
  let bump: (() => void) | undefined;

  function Parent() {
    // 与消息无关的 state：真实场景里 galleryEnabled 异步落地就是这个形状。
    const [unrelated, setUnrelated] = useState(0);
    bump = () => setUnrelated((value) => value + 1);
    return (
      <div>
        <span data-testid="unrelated">{unrelated}</span>
        <MessageGroup messages={messages} isLoading={false} />
      </div>
    );
  }

  act(() => {
    root.render(<Parent />);
  });
  const afterFirst = probe.codeBlockRenders;
  // 前置条件：这条消息确实渲染到了 CodeBlock，计数探针有效。
  expect(afterFirst).toBeGreaterThan(0);

  act(() => {
    bump?.();
  });

  // 父组件确实重渲染了。
  expect(
    container.querySelector('[data-testid="unrelated"]')?.textContent,
  ).toBe("1");
  // 但 MessageGroup 的子树没有被重算。
  expect(probe.codeBlockRenders).toBe(afterFirst);
});

test("messages 内容变化时仍然重渲染——memo 不能把真更新挡掉", () => {
  let setMessages: ((messages: Message[]) => void) | undefined;

  function Parent() {
    const [messages, update] = useState<Message[]>([toolCallMessage("a1")]);
    setMessages = update;
    return <MessageGroup messages={messages} isLoading={false} />;
  }

  act(() => {
    root.render(<Parent />);
  });
  const afterFirst = probe.codeBlockRenders;

  // 只渲染**最后一个** toolCall step，所以改的是那条命令本身，
  // 追加消息不会增加 CodeBlock 数量。
  act(() => {
    setMessages?.([toolCallMessage("a1", "echo changed")]);
  });

  expect(probe.codeBlockRenders).toBeGreaterThan(afterFirst);
  expect(container.textContent).toContain("echo changed");
});

test("isLoading 时只按引用判断——不强制重渲染（避免 FlipDisplay 的 motion 测量死循环）", () => {
  const messages = [toolCallMessage("a1")];
  let bump: (() => void) | undefined;

  function Parent() {
    const [unrelated, setUnrelated] = useState(0);
    bump = () => setUnrelated((value) => value + 1);
    return (
      <div>
        <span>{unrelated}</span>
        <MessageGroup messages={messages} isLoading />
      </div>
    );
  }

  act(() => {
    root.render(<Parent />);
  });
  const afterFirst = probe.codeBlockRenders;

  act(() => {
    bump?.();
  });

  // 父组件确实重渲染了。
  expect(container.textContent).toContain("1");
  // isLoading 但 messages 引用没变 → memo 跳过，不重渲染子树。流式期「显示最新
  // token」由 reuseMessageGroups 给末组换新引用保证（见 group-messages-stability
  // 用例），比较器不再按 isLoading 强制——强制会让所有组加载期都重渲染，触发
  // FlipDisplay 的 motion 测量循环（Maximum update depth，真机打开老任务必现）。
  expect(probe.codeBlockRenders).toBe(afterFirst);
});
