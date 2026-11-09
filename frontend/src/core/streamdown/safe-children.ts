import { useMemo } from "react";
import type { ComponentProps } from "react";
import { type Streamdown } from "streamdown";

import {
  capMarkdownNesting,
  normalizeStreamdownMathMarkdown,
} from "./preprocess";

// （PR-2 3.4：自上游 DeerFlow 同源移植——marked 分块器对深嵌套爆栈/爆堆的
// 预处理防线，配 LLM LaTeX 定界符归一化，经 use* hook 记忆化。）
type StreamdownChildren = ComponentProps<typeof Streamdown>["children"];

export function getSafeStreamdownMarkdown(markdown: string): string {
  return normalizeStreamdownMathMarkdown(capMarkdownNesting(markdown));
}

export function getSafeStreamdownChildren(
  children: StreamdownChildren,
): StreamdownChildren {
  if (typeof children !== "string") {
    return children;
  }

  return getSafeStreamdownMarkdown(children);
}

export function useSafeStreamdownChildren(
  children: StreamdownChildren,
): StreamdownChildren {
  return useMemo(() => getSafeStreamdownChildren(children), [children]);
}

export function useSafeStreamdownMarkdown(markdown: string): string {
  return useMemo(() => getSafeStreamdownMarkdown(markdown), [markdown]);
}
