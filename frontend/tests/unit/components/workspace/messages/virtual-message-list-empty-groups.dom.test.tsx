/**
 * 追加修复回归（规划侧核实 Cursor 报告）：虚拟分支空组漏跳过——修前对每个
 * virtual item 无条件包 absolute 包装 div，renderGroup 返回 null 的组（流式中
 * 暂无内容的 clarification 组等）被 measureElement 量出 pb-8 的 32px 并计入
 * getTotalSize()，长会话留 32px 空行且污染滚动偏移。
 *
 * 钉三条：①虚拟分支 DOM 无空包装 div；②总高度不含空组份额（未实测前
 * = 176 估算 × 可渲染组数，而非 × 全量组数）；③静态分支同语义。
 *
 * 仓库无 @testing-library/react：createRoot + act + happy-dom 手写 harness。
 * use-stick-to-bottom 需要 Provider——直接用库的 StickToBottom 组件包裹。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { StickToBottom } from "use-stick-to-bottom";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { VirtualMessageList } from "@/components/workspace/messages/virtual-message-list";
import type { ThreadMessageGroup } from "@/core/messages/utils";

const TOTAL_GROUPS = 70;
// index % 3 === 1 的组渲染 null（共 23 组）→ 可渲染 47 组。
const RENDERABLE_COUNT = 47;
const ESTIMATED_ROW_HEIGHT = 176;

function makeGroups(): ThreadMessageGroup[] {
  return Array.from({ length: TOTAL_GROUPS }, (_, i) => ({
    id: `g-${i}`,
    type: "assistant",
    messages: [],
  })) as unknown as ThreadMessageGroup[];
}

function renderGroupWithNulls(
  group: ThreadMessageGroup,
  index: number,
): React.ReactNode {
  if (index % 3 === 1) return null;
  return <div data-testid={`group-content-${index}`}>{group.id}</div>;
}

describe("VirtualMessageList 空组不进布局（追加修复）", () => {
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

  test(`>60 组且含 null 渲染组：无空包装 div，总高度 = ${RENDERABLE_COUNT} 组份额`, async () => {
    await act(async () => {
      root?.render(
        <StickToBottom>
          <StickToBottom.Content>
            <VirtualMessageList
              groups={makeGroups()}
              isLoading={false}
              renderGroup={renderGroupWithNulls}
            />
          </StickToBottom.Content>
        </StickToBottom>,
      );
    });

    const wrappers = container?.querySelectorAll("[data-message-group-index]");
    // ① 渲染窗内无空包装 div：每个包装都有实际内容子节点。（happy-dom 无真
    // 布局，窗口可能为空——空组不产生包装 div 的结构性保证来自过滤视图数组，
    // DOM 级证明由静态分支用例承担；此处对「渲染出的任何包装」做非空断言。）
    for (const wrapper of wrappers ?? []) {
      expect(wrapper.childElementCount).toBeGreaterThan(0);
    }
    for (const wrapper of wrappers ?? []) {
      expect(wrapper.childElementCount).toBeGreaterThan(0);
    }
    // 窗口内渲染的组都不是 null 组（null 组索引 mod 3 === 1）。
    for (const wrapper of wrappers ?? []) {
      const idx = Number(wrapper.getAttribute("data-message-group-index"));
      expect(idx % 3 === 1).toBe(false);
    }

    // ② 总高度不含空组：未实测状态下 = 估算 176 × 可渲染组数。
    const listEl = container?.querySelector(".relative.w-full");
    expect(listEl).toBeTruthy();
    const height = Number.parseFloat(
      listEl?.getAttribute("style")?.match(/height:\s*([\d.]+)px/)?.[1] ?? "",
    );
    expect(height).toBe(RENDERABLE_COUNT * ESTIMATED_ROW_HEIGHT);
    // 反证：若空组被计入（全量 70 组），高度会是 70×176=12320。
    expect(height).not.toBe(TOTAL_GROUPS * ESTIMATED_ROW_HEIGHT);
  });

  test("<60 组静态分支：同语义（空组不渲染包装 div）", async () => {
    const groups = makeGroups().slice(0, 50); // 空组 17 个（1,4,…,49）→ 可渲染 33
    await act(async () => {
      root?.render(
        <StickToBottom>
          <StickToBottom.Content>
            <VirtualMessageList
              groups={groups}
              isLoading={false}
              renderGroup={renderGroupWithNulls}
            />
          </StickToBottom.Content>
        </StickToBottom>,
      );
    });
    const wrappers = container?.querySelectorAll("[data-message-group-index]");
    expect(wrappers?.length).toBe(33);
    for (const wrapper of wrappers ?? []) {
      expect(wrapper.childElementCount).toBeGreaterThan(0);
    }
  });
});
