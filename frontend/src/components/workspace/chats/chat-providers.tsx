"use client";

/**
 * chats 会话壳（2026-10-06 合并批）：Subtasks → Artifacts → PromptInput 三层
 * provider 顺序原在两个 [thread_id]/layout 逐字重复。
 * ?msg= 预填差异保留在调用方（普通 chats 在 layout 读取并传 initialInput；
 * agent chats 走页面 useEffect 方案 A，见其 layout 注释）。
 */
import type { ReactNode } from "react";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { SubtasksProvider } from "@/core/tasks/context";

export function ChatProviders({
  children,
  initialInput,
}: {
  children: ReactNode;
  initialInput?: string;
}) {
  return (
    <SubtasksProvider>
      <ArtifactsProvider>
        <PromptInputProvider initialInput={initialInput}>
          {children}
        </PromptInputProvider>
      </ArtifactsProvider>
    </SubtasksProvider>
  );
}
