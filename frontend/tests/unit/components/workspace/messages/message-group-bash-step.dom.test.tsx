/**
 * P1-6：bash 工具步骤渲染成无样式裸文字。
 *
 * `message-group.tsx` 的 bash 分支在没有 `description` 时 **return 了一个裸
 * 字符串**，而三个兄弟分支（ls / read_file / write_file）都是**赋值**给
 * description 再统一走 ChainOfThoughtStep。更伤的是这个 early return 发生在
 * 读 `args.command` **之前**——命令代码块整块被跳过，用户看不到 agent 到底
 * 执行了什么命令。
 *
 * 仓库无 @testing-library/react：沿用 subtask-card.test.tsx 的 createRoot +
 * act + happy-dom 手写 harness。
 */

import type { Message } from "@langchain/langgraph-sdk";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// CodeBlock 走 shiki 的异步 codeToHtml，测试环境里高亮结果永远来不及落地。
// 换成同步 stub，直接钉住「命令有没有交给代码块」这一件事。
vi.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ code, language }: { code: string; language: string }) => (
    <pre data-testid="code-block" data-language={language}>
      {code}
    </pre>
  ),
}));

// MarkdownContent 在模块级 import katex CSS，vitest 无 CSS transform。
vi.mock("@/components/workspace/messages/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <div>{content}</div>,
}));

// useArtifacts 需要 ArtifactsProvider（内部又依赖 sidebar context）；
// 本用例不走 write_file 的 artifact 选中路径，给最小实现。
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
        listFolder: "列出文件夹",
        readFile: "读取文件",
        writeFile: "写入文件",
        needYourHelp: "需要你的协助",
        writeTodos: "更新 To-do 列表",
        useTool: (toolName: string) => `使用 “${toolName}” 工具`,
      },
    },
  }),
}));

import { MessageGroup } from "@/components/workspace/messages/message-group";

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

function bashMessages(args: Record<string, unknown>): Message[] {
  return [
    {
      type: "ai",
      id: "ai-1",
      content: "",
      tool_calls: [{ id: "tc-1", name: "bash", args }],
    } as unknown as Message,
  ];
}

async function render(messages: Message[]) {
  await act(async () => {
    root.render(<MessageGroup messages={messages} isLoading={false} />);
  });
}

describe("bash tool step", () => {
  test("renders icon + command code block when description is missing", async () => {
    await render(bashMessages({ command: "echo hello" }));

    expect(caughtErrors).toEqual([]);
    // 兜底文案仍要显示
    expect(container.textContent).toContain("执行命令");
    // 图标：裸字符串分支下整个 ChainOfThoughtStep（含 SquareTerminalIcon）都不渲染
    expect(container.querySelector("svg")).not.toBeNull();
    // 最伤的一条：early return 发生在读 command 之前，命令代码块整块被跳过
    const codeBlock = container.querySelector('[data-testid="code-block"]');
    expect(codeBlock).not.toBeNull();
    expect(codeBlock?.textContent).toBe("echo hello");
    expect(codeBlock?.getAttribute("data-language")).toBe("bash");
  });

  test("keeps rendering description + command when description is present", async () => {
    await render(
      bashMessages({ description: "列出依赖", command: "pip list" }),
    );

    expect(caughtErrors).toEqual([]);
    expect(container.textContent).toContain("列出依赖");
    expect(
      container.querySelector('[data-testid="code-block"]')?.textContent,
    ).toBe("pip list");
  });
});
