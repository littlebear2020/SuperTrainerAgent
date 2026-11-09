/**
 * PR-2 行为级断言（plan §5 验证清单）：流式进行中的代码块走纯文本态
 * （StreamingPre/StreamingCode，data-streaming-code-block 标记 + 无高亮
 * token span）——完结后（isLoading=false）交还 @streamdown/code 的 shiki
 * 高亮路径，纯文本壳必须退场。
 *
 * 仓库无 @testing-library/react：沿用 createRoot + act + happy-dom 手写
 * harness（同 message-group-memo.dom.test.tsx）。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { MarkdownContent } from "@/components/workspace/messages/markdown-content";

const STREAMING_CODE_MD = "```ts\nconst answer = 42;\n```";

describe("MarkdownContent 流式代码块纯文本态（PR-2）", () => {
  let container: HTMLDivElement | null = null;
  let root: Root | null = null;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    container?.remove();
    container = null;
    root = null;
  });

  test("isLoading=true：代码块带 streaming 标记且为纯文本（无高亮 token）", async () => {
    await act(async () => {
      root?.render(
        <MarkdownContent content={STREAMING_CODE_MD} isLoading={true} />,
      );
    });

    const streamingBlock = container?.querySelector(
      "[data-streaming-code-block]",
    );
    expect(streamingBlock).toBeTruthy();
    // 纯文本态：pre 子树内没有 shiki/highlight token span，代码原文直接在文本层
    // （块外层的语言标签 chip span 不算高亮 token）。
    const pre = streamingBlock?.querySelector("pre");
    const tokenSpans = pre?.querySelectorAll("span[class]");
    expect(tokenSpans?.length ?? 0).toBe(0);
    expect(pre?.textContent).toContain("const answer = 42;");
  });

  test("isLoading=false：纯文本壳退场（交还高亮路径）", async () => {
    await act(async () => {
      root?.render(
        <MarkdownContent content={STREAMING_CODE_MD} isLoading={true} />,
      );
    });
    expect(
      container?.querySelector("[data-streaming-code-block]"),
    ).toBeTruthy();

    await act(async () => {
      root?.render(
        <MarkdownContent content={STREAMING_CODE_MD} isLoading={false} />,
      );
    });
    expect(container?.querySelector("[data-streaming-code-block]")).toBeNull();
    // 内容不丢：完结态仍渲染出代码文本。
    expect(container?.textContent).toContain("const answer = 42;");
  });
});
