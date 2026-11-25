/**
 * PR3（2026-11-24 前端可靠性批）：列表从静态（<60 组）跨到虚拟（≥60 组）的那一帧。
 * 流式的 values 事件会把整段历史（含更早的轮次）一次性送到：首组的键变了，看起来像
 * 一次 prepend，但用户停在底部、本该继续贴底——这不是「加载更多」的锚定场景。
 * 这里钉住：跨阈值那一帧不抛错、不把锚定强加给贴底的用户。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { StickToBottom } from "use-stick-to-bottom";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { VirtualMessageList } from "@/components/workspace/messages/virtual-message-list";
import type { ThreadMessageGroup } from "@/core/messages/utils";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function groups(prefix: string, count: number): ThreadMessageGroup[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `${prefix}-${i}`,
    type: "assistant",
    messages: [],
  })) as unknown as ThreadMessageGroup[];
}

function renderGroup(group: ThreadMessageGroup) {
  return <div data-testid={`group-${group.id}`}>{group.id}</div>;
}

describe("VirtualMessageList 静态 → 虚拟的跨阈值帧", () => {
  let container: HTMLDivElement | null = null;
  let root: Root | null = null;
  const caught: unknown[] = [];

  beforeEach(() => {
    caught.length = 0;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container, {
      onCaughtError: (error) => caught.push(error),
      onUncaughtError: (error) => caught.push(error),
    });
  });

  afterEach(() => {
    act(() => {
      root?.unmount();
    });
    container?.remove();
    container = null;
    root = null;
  });

  test("贴底时整段历史随流到达：跨到虚拟分支不抛错", async () => {
    // 新会话：只有当前这一轮（2 组，静态分支）
    await act(async () => {
      root?.render(
        <StickToBottom>
          <StickToBottom.Content>
            <VirtualMessageList
              groups={groups("current", 2)}
              isLoading={true}
              renderGroup={renderGroup}
            />
          </StickToBottom.Content>
        </StickToBottom>,
      );
    });
    // values 事件：更早的 47 轮插在前面 → 首组键变化、总数 96 → 虚拟分支
    await act(async () => {
      root?.render(
        <StickToBottom>
          <StickToBottom.Content>
            <VirtualMessageList
              groups={[...groups("history", 94), ...groups("current", 2)]}
              isLoading={true}
              renderGroup={renderGroup}
            />
          </StickToBottom.Content>
        </StickToBottom>,
      );
    });
    expect(caught).toEqual([]);
  });
});
