"use client";

import {
  defaultRangeExtractor,
  useVirtualizer,
  type Range,
} from "@tanstack/react-virtual";
import {
  forwardRef,
  useCallback,
  useEffect,
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
/** prepend 后按同一锚点校正滚动的最多次数（未测高行逐批变成实测高度）。 */
const PREPEND_ANCHOR_MAX_ADJUSTMENTS = 8;
/** 会移动滚动位置的按键：锚定期间按下即视为用户接手。 */
const NAVIGATION_KEYS = new Set([
  "ArrowUp",
  "ArrowDown",
  "PageUp",
  "PageDown",
  "Home",
  "End",
  " ",
]);
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
  /**
   * 锚定期间强制渲染锚点行（PR3）：prepend 后新插入的行只有估计高度，按估计跳转未必
   * 把锚点行带进渲染窗口；把它钉进渲染集合，才能按它的真实 DOM 位置校正。
   * （useVirtualizer 在渲染期同步调用 rangeExtractor，声明必须在它之前。）
   */
  const forcedRenderIndexRef = useRef<number | null>(null);
  const rangeExtractor = useCallback(
    (range: Range) => {
      const indices = defaultRangeExtractor(range);
      // 行数在锚定期间可能变化（optimistic 消息被真实消息替换）：越界的索引会得到
      // 不存在的 measurement，虚拟列表渲染时就是 undefined 行
      const forced = forcedRenderIndexRef.current;
      if (
        forced !== null &&
        forced >= 0 &&
        forced < range.count &&
        !indices.includes(forced)
      ) {
        indices.push(forced);
        indices.sort((a, b) => a - b);
      }
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
  /**
   * 虚拟分支的锚点：首个可见行的键、视口内偏移，以及它当时相对滚动容器顶部的真实
   * DOM 距离。
   */
  const anchorRef = useRef<
    { key: Key; viewportOffset: number; domTop?: number } | undefined
  >(undefined);
  /**
   * prepend 后待校正的锚点：首次对齐时新插入的行还是估计高度，实测高度陆续到位后
   * 锚点行的位置会再移动——之后每一帧按锚点行**真实的 DOM 位置**校正，直到稳定。
   * 估计坐标（getOffsetForIndex）与实测来回变化时不采用，只认 DOM。
   */
  const pendingAnchorRef = useRef<
    | {
        key: Key;
        viewportOffset: number;
        domTop?: number;
        attempts: number;
        /** 刚按估计高度跳转、渲染窗口还没跟上：下一帧才开始按 DOM 校正。 */
        awaitingRender?: boolean;
        /** 锚点行连续不在渲染窗口的帧数。 */
        misses?: number;
      }
    | undefined
  >(undefined);
  /** 用户在锚定期间自己滚了（滚轮 / 触摸 / 拖滚动条 / 方向键）：锚定让位。 */
  const userScrolledRef = useRef(false);
  const clearPendingAnchor = useCallback(() => {
    pendingAnchorRef.current = undefined;
    forcedRenderIndexRef.current = null;
  }, []);
  /**
   * 静态分支（<60 组）的锚点：首个可见行的键与它相对滚动容器顶部的位置。记相对坐标：
   * 容器自己在两次 prepend 之间移动（头部变高、resize）时不会失真。静态态没有测量
   * 数据，getOffsetForIndex 全是估算坐标，只能按真实 DOM 节点锚定；浏览器的原生滚动
   * 锚定在滚动位置为 0 时不下调（Chrome 刻意如此），所以不能全交给它。
   */
  const staticAnchorRef = useRef<{ key: Key; domOffset: number } | undefined>(
    undefined,
  );
  /** 上一次锚定 effect 运行时的滚动状态：变了说明用户滚过，首个可见行要重新记。 */
  const lastScrollStateRef = useRef<{
    isScrolling: boolean;
    offset: number | null;
  }>({ isScrolling: false, offset: null });
  const wasVirtualRef = useRef(false);

  const viewIndexForKey = useCallback(
    (key: Key) =>
      renderableViews.findIndex(
        (view, viewIndex) => getItemKey(viewIndex) === key,
      ),
    [renderableViews, getItemKey],
  );
  const rowForView = useCallback((view: RenderableView | undefined) => {
    return view
      ? (listRef.current?.querySelector<HTMLElement>(
          `[data-message-group-index="${view.index}"]`,
        ) ?? null)
      : null;
  }, []);

  /** 静态分支里首个可见的行（底边越过视口顶部的第一行）。 */
  const firstVisibleStaticAnchor = useCallback(
    (viewport: HTMLElement) => {
      const viewportTop = viewport.getBoundingClientRect().top;
      for (
        let viewIndex = 0;
        viewIndex < renderableViews.length;
        viewIndex += 1
      ) {
        const row = rowForView(renderableViews[viewIndex]);
        if (!row) continue;
        const rect = row.getBoundingClientRect();
        if (rect.bottom > viewportTop) {
          return {
            key: getItemKey(viewIndex),
            domOffset: rect.top - viewportTop,
          };
        }
      }
      return undefined;
    },
    [renderableViews, getItemKey, rowForView],
  );

  useEffect(() => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    const mark = () => {
      userScrolledRef.current = true;
    };
    const onKey = (event: KeyboardEvent) => {
      // 输入框里打空格、按方向键不是在滚列表：只有事件目标不在可编辑元素上才算
      const target = event.target as HTMLElement | null;
      const editable =
        !!target &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
      if (!editable && NAVIGATION_KEYS.has(event.key)) mark();
    };
    // 消息里的按钮（复制、打开工件、展开卡片）也落在视口里：点击本身不是滚动意图，
    // 只有按在滚动条上才算用户接手
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY !== 0) mark();
    };
    const onPointerDown = (event: PointerEvent) => {
      const rect = viewport.getBoundingClientRect();
      const SCROLLBAR = 16;
      if (
        event.clientX >= rect.right - SCROLLBAR ||
        event.clientY >= rect.bottom - SCROLLBAR
      ) {
        mark();
      }
    };
    viewport.addEventListener("wheel", onWheel, { passive: true });
    viewport.addEventListener("touchmove", mark, { passive: true });
    viewport.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKey);
    return () => {
      viewport.removeEventListener("wheel", onWheel);
      viewport.removeEventListener("touchmove", mark);
      viewport.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [scrollRef]);

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
    const viewport = scrollRef.current;
    const firstKey = getItemKey(0);
    const previousFirstKey = previousFirstKeyRef.current;
    // prepend 推演（含空组）：键 = 组 id（见 groupKey），头部插入历史组只让 firstKey
    // 变化、不移位既有键；锚点恢复按**键**反查视图索引，空组不在布局中、对偏移
    // 零贡献——恢复坐标与无空组时同构。
    const prepended =
      previousFirstKey !== undefined && firstKey !== previousFirstKey;
    previousFirstKeyRef.current = firstKey;
    const wasVirtual = wasVirtualRef.current;
    wasVirtualRef.current = shouldVirtualize;
    // 自上次运行以来用户滚过（滚动中标志翻转——滚动开始 / 停下 150ms 后各一次——或
    // 滚动偏移变了）：首个可见行可能换了，静态锚点要跟着重记
    const scrollState = {
      isScrolling: virtualizer.isScrolling,
      offset: virtualizer.scrollOffset,
    };
    const scrolledSinceLastRun =
      scrollState.isScrolling !== lastScrollStateRef.current.isScrolling ||
      scrollState.offset !== lastScrollStateRef.current.offset;
    lastScrollStateRef.current = scrollState;

    if (!shouldVirtualize) {
      // 静态分支：没有测量数据，按真实 DOM 节点锚定（PR3）；虚拟态的 refs 清零，
      // 防「静态期锚点污染首次虚拟帧」
      anchorRef.current = undefined;
      clearPendingAnchor();
      const anchor = staticAnchorRef.current;
      if (prepended && anchor && viewport && !isAtBottom) {
        const row = rowForView(renderableViews[viewIndexForKey(anchor.key)]);
        if (row) {
          const delta =
            row.getBoundingClientRect().top -
            (viewport.getBoundingClientRect().top + anchor.domOffset);
          if (Math.abs(delta) > 1) viewport.scrollTop += delta;
        }
      }
      // 首个可见行只在 prepend（上面的校正之后）或用户滚动之后才变；流式追加只动底部，
      // 不必每个 chunk 都在 layout effect 里量 DOM（修前静态分支是零 DOM 读写）。
      // 不能借 previousCountRef 判断行数变化：上面的 settle effect 先运行并把它同步成
      // 当前值，这里的比较恒为 false，锚点会一直停在上一次 prepend 时的首行
      if (prepended || scrolledSinceLastRun || !staticAnchorRef.current) {
        staticAnchorRef.current = viewport
          ? firstVisibleStaticAnchor(viewport)
          : undefined;
      }
      return;
    }

    if (!wasVirtual) {
      // 静态 → 虚拟（prepend 跨过阈值）：把静态锚点交给虚拟分支的校正流程
      const staticAnchor = staticAnchorRef.current;
      staticAnchorRef.current = undefined;
      if (prepended && staticAnchor && viewport && !isAtBottom) {
        const anchorViewIndex = viewIndexForKey(staticAnchor.key);
        if (anchorViewIndex >= 0) {
          anchorRef.current = {
            key: staticAnchor.key,
            viewportOffset: staticAnchor.domOffset,
            domTop: staticAnchor.domOffset,
          };
        }
      }
    }

    const anchor = anchorRef.current;
    // 贴底时到达的「前面多出来的行」是流式 values 事件送来的整段历史（或追加），
    // 不是用户在顶部点「加载更多」：交给 stick-to-bottom 继续贴底，不锚定
    if (prepended && anchor && !isAtBottom) {
      pendingAnchorRef.current = {
        ...anchor,
        attempts: 0,
        awaitingRender: true,
      };
      userScrolledRef.current = false;
      // 先按估计高度把锚点行拉回渲染窗口；实测高度到位后由下面的校正按 DOM 修正
      const anchorViewIndex = viewIndexForKey(anchor.key);
      forcedRenderIndexRef.current = anchorViewIndex;
      const estimated =
        anchorViewIndex >= 0
          ? virtualizer.getOffsetForIndex(anchorViewIndex, "start")?.[0]
          : undefined;
      if (estimated !== undefined) {
        virtualizer.scrollToOffset(estimated - anchor.viewportOffset, {
          behavior: "auto",
        });
      }
    }

    const pending = pendingAnchorRef.current;
    if (pending?.awaitingRender) {
      // 刚跳转：渲染窗口还没跟上，下一帧再量
      pending.awaitingRender = false;
    } else if (pending && userScrolledRef.current) {
      // 用户已经接手：不再校正
      clearPendingAnchor();
    } else if (pending) {
      const row = rowForView(renderableViews[viewIndexForKey(pending.key)]);
      if (!row || !viewport) {
        // 锚点行暂时不在渲染窗口：给渲染两三帧，再不在就不校正
        pending.misses = (pending.misses ?? 0) + 1;
        if (pending.misses > 2) clearPendingAnchor();
      } else {
        // domTop 记的是相对滚动容器顶部的距离：容器自己移动（resize、头部变高）后按
        // 当前容器位置换算，不会把锚点行校正到旧位置
        const viewportTop = viewport.getBoundingClientRect().top;
        const desiredTop =
          viewportTop + (pending.domTop ?? pending.viewportOffset);
        const delta = row.getBoundingClientRect().top - desiredTop;
        if (Math.abs(delta) <= 1) {
          clearPendingAnchor();
        } else {
          viewport.scrollTop += delta;
          pending.attempts += 1;
          if (pending.attempts >= PREPEND_ANCHOR_MAX_ADJUSTMENTS) {
            clearPendingAnchor();
          }
          // 刚改了滚动位置：下面的锚点刷新读的是校正前的坐标，留到滚动后的下一帧
          return;
        }
      }
    }

    const scrollOffset = virtualizer.scrollOffset ?? 0;
    const firstVisible = virtualItems.find((item) => item.end >= scrollOffset);
    if (firstVisible) {
      const row = rowForView(renderableViews[firstVisible.index]);
      anchorRef.current = {
        key: firstVisible.key,
        viewportOffset: firstVisible.start - scrollOffset,
        domTop:
          row && viewport
            ? row.getBoundingClientRect().top -
              viewport.getBoundingClientRect().top
            : undefined,
      };
    }
  }, [
    clearPendingAnchor,
    firstVisibleStaticAnchor,
    getItemKey,
    isAtBottom,
    renderableViews,
    rowForView,
    scrollRef,
    shouldVirtualize,
    viewIndexForKey,
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
