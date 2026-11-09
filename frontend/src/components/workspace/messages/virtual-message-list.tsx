"use client";

import {
  defaultRangeExtractor,
  useVirtualizer,
  type Range,
} from "@tanstack/react-virtual";
import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  type Key,
  type ReactNode,
} from "react";
import { useStickToBottomContext } from "use-stick-to-bottom";

import type { ThreadMessageGroup } from "@/core/messages/utils";

// （PR-2 4.2：自上游 DeerFlow virtual-message-list.tsx 近原样移植；按 plan 裁
// onActiveGroupChange（本地无会话大纲消费面）。要点：≥60 组才虚拟化；行高
// measureElement 实测；流式末组常驻渲染窗（rangeExtractor 并入 activeIndex）；
// 头部 prepend 锚定（anchorRef 记首可见行 key+视口偏移）；首挂载双帧定位
// （估计高度定位一帧 + 实测后重锚一帧）。
//
// 追加修复（规划侧核实 Cursor 报告）：renderGroup 返回 null 的空组（流式中
// 暂无内容的 clarification 组等）在虚拟分支也必须不渲染、不测高、不计入
// getTotalSize——修前虚拟分支对每个 virtual item 无条件包 absolute 包装 div，
// 空组被 measureElement 量出 pb-8 的 32px 并污染滚动偏移。方案：先把
// renderGroup 全量求值成视图数组（元素创建是廉价 vdom 分配，只有进渲染窗
// 才真正挂载），滤掉空内容后交给虚拟器；**键仍取组 id、行上保留原始组
// 索引**（data-message-group-index / renderAfterGroup / scrollToGroup 语义
// 不变），虚拟器索引只用于窗口计算——prepend/填充转移不会移位既有键，
// tanstack 的尺寸测量按 getItemKey 关联，实测行高随组走不随位走。）
const VIRTUALIZATION_THRESHOLD = 60;
const ESTIMATED_ROW_HEIGHT = 176;
const GROUP_START_OFFSET = 16;
const VIRTUAL_SCROLL_SETTLE_ATTEMPTS = 4;
const STATIC_SCROLL_SETTLE_ATTEMPTS = 2;

type GroupAlignment = "start" | "center";

type ScrollToGroupOptions = {
  behavior?: ScrollBehavior;
  align?: GroupAlignment;
};

type VirtualMessageListProps = {
  groups: readonly ThreadMessageGroup[];
  isLoading: boolean;
  renderGroup: (group: ThreadMessageGroup, index: number) => ReactNode;
  /**
   * Extra content rendered after a group inside its row, so it is measured
   * and scrolled with it.
   */
  renderAfterGroup?: (index: number) => ReactNode;
};

export type VirtualMessageListHandle = {
  scrollToGroup: (groupIndex: number, options?: ScrollToGroupOptions) => void;
};

type RenderableView = {
  /** 原始组（供 renderAfterGroup/查询语义）。 */
  group: ThreadMessageGroup;
  /** 在 groups 全量数组中的原始索引（空组过滤不移位）。 */
  index: number;
  content: ReactNode;
};

function isRenderable(content: ReactNode): boolean {
  return content !== null && content !== undefined && content !== false;
}

function groupKey(group: ThreadMessageGroup | undefined, index: number): Key {
  return (
    group?.id ??
    group?.messages.find((message) => message.id)?.id ??
    `${group?.type ?? "message"}:${index}`
  );
}

export const VirtualMessageList = forwardRef<
  VirtualMessageListHandle,
  VirtualMessageListProps
>(function VirtualMessageList(
  { groups, isLoading, renderGroup, renderAfterGroup },
  ref,
) {
  const { isAtBottom, scrollRef, scrollToBottom, stopScroll } =
    useStickToBottomContext();
  const listRef = useRef<HTMLDivElement | null>(null);

  // 空组过滤的单一事实源：全量求值 renderGroup（元素创建 ≠ 挂载，开销是
  // O(n) 的 vdom 对象分配），静态/虚拟两分支消费同一视图数组——语义一致。
  const renderableViews = useMemo(
    () =>
      groups
        .map((group, index): RenderableView => {
          // 静态分支调用带全量索引，视图数组沿用同一索引值。
          return { group, index, content: renderGroup(group, index) };
        })
        .filter((view) => isRenderable(view.content)),
    [groups, renderGroup],
  );
  const viewCount = renderableViews.length;
  const activeIndex = isLoading ? viewCount - 1 : -1;
  const getItemKey = useCallback(
    (viewIndex: number) => {
      const view = renderableViews[viewIndex];
      return view ? groupKey(view.group, view.index) : `${viewIndex}`;
    },
    [renderableViews],
  );
  const rangeExtractor = useCallback(
    (range: Range) => {
      const indices = defaultRangeExtractor(range);
      if (activeIndex >= 0 && !indices.includes(activeIndex)) {
        indices.push(activeIndex);
        indices.sort((a, b) => a - b);
      }
      return indices;
    },
    [activeIndex],
  );
  const virtualizer = useVirtualizer({
    count: viewCount,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    getItemKey,
    getScrollElement: () => scrollRef.current,
    overscan: 8,
    rangeExtractor,
  });
  const virtualItems = virtualizer.getVirtualItems();
  // 阈值口径 = 全量组数（上游/plan 文档语义「≥60 组」）；空组已从布局剔除，
  // 不影响该判据的成本含义（空组本来就是瞬时态）。
  const shouldVirtualize = groups.length >= VIRTUALIZATION_THRESHOLD;
  const positionedInitialVirtualWindowRef = useRef(false);
  const previousCountRef = useRef(viewCount);
  const previousFirstKeyRef = useRef<Key | undefined>(undefined);
  const anchorRef = useRef<{ key: Key; viewportOffset: number } | undefined>(
    undefined,
  );

  const alignGroupToViewport = useCallback(
    (
      groupIndex: number,
      align: GroupAlignment,
      behavior: ScrollBehavior,
    ): boolean => {
      const viewport = scrollRef.current;
      const row = listRef.current?.querySelector<HTMLElement>(
        `[data-message-group-index="${groupIndex}"]`,
      );
      if (!viewport || !row) {
        return false;
      }

      const viewportRect = viewport.getBoundingClientRect();
      const rowRect = row.getBoundingClientRect();
      const top =
        viewport.scrollTop +
        rowRect.top -
        viewportRect.top -
        (align === "center"
          ? Math.max(0, (viewport.clientHeight - rowRect.height) / 2)
          : GROUP_START_OFFSET);
      viewport.scrollTo({ top: Math.max(0, top), behavior });
      return true;
    },
    [scrollRef],
  );

  useImperativeHandle(
    ref,
    () => ({
      scrollToGroup(groupIndex, options) {
        // 入参语义 = 原始组索引；空组无行可滚，直接 no-op。
        if (groupIndex < 0 || groupIndex >= groups.length) {
          return;
        }
        const viewIndex = renderableViews.findIndex(
          (view) => view.index === groupIndex,
        );
        if (viewIndex < 0) {
          return;
        }
        stopScroll();
        const behavior = options?.behavior ?? "auto";
        const align = options?.align ?? "start";
        if (shouldVirtualize) {
          virtualizer.scrollToIndex(viewIndex, { align, behavior });
        }

        const maxAttempts = shouldVirtualize
          ? VIRTUAL_SCROLL_SETTLE_ATTEMPTS
          : STATIC_SCROLL_SETTLE_ATTEMPTS;
        let attempt = 0;
        const settleOnExactGroup = () => {
          const exactBehavior = attempt === 0 ? behavior : "auto";
          const aligned = alignGroupToViewport(
            groupIndex,
            align,
            exactBehavior,
          );
          attempt += 1;
          const needsAnotherAttempt = shouldVirtualize || !aligned;
          if (attempt < maxAttempts && needsAnotherAttempt) {
            requestAnimationFrame(settleOnExactGroup);
          }
        };
        requestAnimationFrame(settleOnExactGroup);
      },
    }),
    [
      alignGroupToViewport,
      groups.length,
      renderableViews,
      shouldVirtualize,
      stopScroll,
      virtualizer,
    ],
  );

  useLayoutEffect(() => {
    let settleFrame: number | undefined;
    if (
      shouldVirtualize &&
      isAtBottom &&
      !positionedInitialVirtualWindowRef.current
    ) {
      positionedInitialVirtualWindowRef.current = true;
      const scrollToLatest = () => {
        virtualizer.scrollToIndex(viewCount - 1, { align: "end" });
      };
      scrollToLatest();
      // Dynamic row measurement changes the total after the first layout.
      // Re-anchor once with measured sizes so a long restored conversation
      // cannot land around the estimated midpoint.
      settleFrame = requestAnimationFrame(scrollToLatest);
    } else if (!shouldVirtualize) {
      positionedInitialVirtualWindowRef.current = false;
    }
    if (viewCount > previousCountRef.current && isAtBottom) {
      void scrollToBottom({
        animation: "instant",
        preserveScrollPosition: true,
      });
    }
    previousCountRef.current = viewCount;
    return () => {
      if (settleFrame !== undefined) cancelAnimationFrame(settleFrame);
    };
  }, [isAtBottom, scrollToBottom, shouldVirtualize, viewCount, virtualizer]);

  useLayoutEffect(() => {
    // 评审 important 订正：prepend 锚定只属虚拟态——静态分支（<60 组）行高
    // 停留在 estimateSize（measureElement 未挂载），getOffsetForIndex 全是估算
    // 坐标，主动 scrollToOffset 只会把视口甩到错误位置；静态态交还浏览器原生
    // 滚动锚定（修前行为）。refs 清零防「静态期锚点污染首次虚拟帧」。
    if (!shouldVirtualize) {
      previousFirstKeyRef.current = undefined;
      anchorRef.current = undefined;
      return;
    }
    const firstKey = getItemKey(0);
    const previousFirstKey = previousFirstKeyRef.current;
    const anchor = anchorRef.current;
    // prepend 推演（含空组）：键 = 组 id（见 groupKey），头部插入历史组只让
    // firstKey 变化、不移位既有键；锚点恢复按**键**反查视图索引，空组不在
    // 布局中、对偏移零贡献——恢复坐标与无空组时同构。
    if (
      previousFirstKey !== undefined &&
      firstKey !== previousFirstKey &&
      anchor
    ) {
      const anchorViewIndex = renderableViews.findIndex(
        (view, viewIndex) => getItemKey(viewIndex) === anchor.key,
      );
      if (anchorViewIndex >= 0) {
        const anchorStart = virtualizer.getOffsetForIndex(
          anchorViewIndex,
          "start",
        )?.[0];
        if (anchorStart !== undefined) {
          virtualizer.scrollToOffset(anchorStart - anchor.viewportOffset, {
            behavior: "auto",
          });
        }
      }
    }
    previousFirstKeyRef.current = firstKey;

    const scrollOffset = virtualizer.scrollOffset ?? 0;
    const firstVisible = virtualItems.find((item) => item.end >= scrollOffset);
    if (firstVisible) {
      anchorRef.current = {
        key: firstVisible.key,
        viewportOffset: firstVisible.start - scrollOffset,
      };
    }
  }, [
    getItemKey,
    renderableViews,
    shouldVirtualize,
    virtualItems,
    virtualizer,
  ]);

  if (!shouldVirtualize) {
    return (
      <div ref={listRef} className="flex flex-col gap-8">
        {renderableViews.map((view) => (
          <div
            key={groupKey(view.group, view.index)}
            data-message-group-index={view.index}
          >
            {view.content}
            {renderAfterGroup?.(view.index)}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div
      ref={listRef}
      className="relative w-full"
      style={{ height: `${virtualizer.getTotalSize()}px` }}
    >
      {virtualItems.map((virtualRow) => {
        const view = renderableViews[virtualRow.index];
        if (!view) return null;
        return (
          <div
            key={virtualRow.key}
            ref={virtualizer.measureElement}
            data-index={virtualRow.index}
            data-message-group-index={view.index}
            className="absolute top-0 left-0 w-full pb-8"
            style={{ transform: `translateY(${virtualRow.start}px)` }}
          >
            {view.content}
            {renderAfterGroup?.(view.index)}
          </div>
        );
      })}
    </div>
  );
});
