/**
 * Task 7（P0，2026-08-01 服务器反馈 2）：BGM 选择器在 Dialog 内**不可点**。
 *
 * 用户现象：高级设置里打开 BGM 选择器，曲目肉眼可见但一首都点不中。
 *
 * 根因（真实 Chromium A/B 实测坐实，非推断）：pnpm 把
 * @radix-ui/react-dismissable-layer 装成了多份副本 ——
 *   react-dialog@1.1.15  -> dismissable-layer@1.1.11
 *   react-popover@1.1.17 -> dismissable-layer@1.1.13   ← 不一致
 *   react-select@2.2.6   -> dismissable-layer@1.1.11（与 Dialog 一致，故 Select 正常）
 * 两个版本 = 两个模块实例 = 两个 React context 对象。Dialog(modal) 打开时把
 * body.pointerEvents 置 "none"，再通过**共享 context** 里的 layers 数组把
 * 「层级不低于它」的后开层设回 "auto"。Popover 的 layer 注册在 1.1.13 那份
 * context，Dialog 在 1.1.11 那份 —— Popover 那份看到 size===0 → 干脆不设
 * pointerEvents，而 body 早已是 none → 弹层收不到任何点击。
 *
 * 修复：package.json 的 pnpm.overrides 把 dismissable-layer 钉到单一版本
 * （1.1.13），让所有浮层共享同一份 context。**不是** 提高 z-index —— 层级只管
 * 「谁画在上面」，不管「谁能被点」。
 *
 * 真实 Chromium A/B（修复前 / 修复后，唯一变量 = overrides）：
 *   修复前：popover 能展开，但 pointerEvents=""   → 点选项 3s 超时，选不中
 *   修复后：popover 能展开，且 pointerEvents="auto" → 点中并回填「清晨钢琴」
 *
 * ⚠️ 本文件的断言范围是刻意收窄的。实测 happy-dom **无法**如实模拟
 * 「Dialog 内点 trigger 打开 Popover」这条路径（它会立刻收到一次 dismiss 把
 * 弹层关掉，真实 Chromium 不会）。因此这里**不测点击开合**，只测
 * 「弹层展开态下是否拿到 pointer-events」这个纯 DOM 可断言的不变量 ——
 * 那也正是 bug 本身。点击可选中由 e2e（真实浏览器）守，见
 * tests/e2e/video-gen.spec.ts。
 *
 * 仓库无 @testing-library/react：沿用 storyboard-no-auto-preview.test.tsx 的
 * createRoot + act + happy-dom 手写 harness。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { BgmPicker } from "@/components/workspace/video-gen/bgm-picker";
import type { BGMTrack } from "@/core/video-gen/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const track = (id: string, name: string): BGMTrack =>
  ({
    id,
    name,
    preview_url: `/api/video/bgm/${id}/preview`,
    duration: 120,
    tags: ["测试"],
  }) as BGMTrack;

// 反馈 1「说是 10 首只看到 6 首」用的就是 10 首内置——一并覆盖
const BUILT_IN: BGMTrack[] = [
  track("morning_piano", "清晨钢琴"),
  track("strive", "奋进"),
  track("calm_guitar", "静谧吉他"),
  track("uplift", "昂扬"),
  track("soft_pad", "柔和铺底"),
  track("focus", "专注"),
  track("warm_str", "温暖弦乐"),
  track("light_pop", "轻快流行"),
  track("tech_pulse", "科技脉冲"),
  track("epic", "史诗"),
];

describe("Task 7: Dialog 内的浮层必须可点（dismissable-layer 单副本）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.body.style.pointerEvents = "";
  });

  it("核心不变量：Dialog 置 body{pointer-events:none} 时，其内展开的 Popover 必须拿到 pointer-events:auto", async () => {
    // 直接以 open 态渲染（绕开 happy-dom 无法如实模拟的点击开合路径）
    await act(async () => {
      root.render(
        <Dialog open>
          <DialogContent>
            <Popover open>
              <PopoverTrigger>打开</PopoverTrigger>
              <PopoverContent>
                <div role="option" aria-selected={false}>
                  选项一
                </div>
              </PopoverContent>
            </Popover>
          </DialogContent>
        </Dialog>,
      );
    });

    const pc = document.querySelector<HTMLElement>(
      '[data-slot="popover-content"]',
    );
    expect(pc, "Popover 应已展开").toBeTruthy();

    // 前提：modal Dialog 确实锁了 body 的指针事件（否则本测试没有意义）
    expect(
      document.body.style.pointerEvents,
      "前提不成立：modal Dialog 应把 body 的 pointer-events 置为 none",
    ).toBe("none");

    // 核心断言：副本分裂时这里是 ""（未设置）→ 整个弹层不可点，即用户报的 bug
    expect(
      pc!.style.pointerEvents,
      "Popover 未拿到 pointer-events:auto → 弹层内所有选项都点不动（dismissable-layer 副本分裂）",
    ).toBe("auto");
  });

  it("根因护栏：全站 dismissable-layer 必须只解析出一份副本", async () => {
    // 上一条断言「症状」，这条断言「根因」：依赖树里只允许存在一份实现。
    // 副本一旦再次分裂（升级 Radix / 加新浮层包 / 有人删掉 pnpm.overrides），
    // 此测试立刻转红并直指原因，不必再从「点不动」倒查一整天。
    const { readFileSync, realpathSync } = await import("node:fs");

    // 每个用到浮层的包，实解析它自己那份 dismissable-layer。
    // 不用 require.resolve（Vite 环境下解析行为不同，实测拿不到），
    // 直接按 pnpm 的物理布局走 node_modules symlink + realpath（等价 pwd -P）。
    const consumers = [
      "react-dialog",
      "react-popover",
      "react-select",
      "react-tooltip",
      "react-hover-card",
    ];

    const resolved = new Map<string, string>();
    for (const short of consumers) {
      let real: string;
      try {
        real = realpathSync(`node_modules/@radix-ui/${short}`);
      } catch {
        continue; // 该包未安装则跳过
      }
      // pnpm 布局：<store>/node_modules/@radix-ui/<pkg> 的同级即其依赖
      const sibling = `${real}/../react-dismissable-layer/package.json`;
      try {
        const v = JSON.parse(readFileSync(sibling, "utf8")).version as string;
        resolved.set(short, v);
      } catch {
        // 该包不依赖 dismissable-layer（如 dropdown-menu 经 react-menu 传递）
      }
    }

    expect(
      resolved.size,
      "至少应解析出若干浮层包的 dismissable-layer",
    ).toBeGreaterThan(1);
    const versions = [...new Set(resolved.values())];
    expect(
      versions,
      `dismissable-layer 出现多份副本 → Dialog 内浮层将不可点。实际解析：${JSON.stringify(
        Object.fromEntries(resolved),
      )}`,
    ).toHaveLength(1);
  });

  it("BgmPicker 展开态：10 首内置全在，且分组标题带数量（反馈 1 可发现性）", async () => {
    const onChange = vi.fn();
    await act(async () => {
      root.render(
        <Dialog open>
          <DialogContent>
            <BgmPicker
              builtIn={BUILT_IN}
              custom={[]}
              value={null}
              onChange={onChange}
              playingUrl={null}
              onTogglePreview={vi.fn()}
              defaultOpen
            />
          </DialogContent>
        </Dialog>,
      );
    });

    const pc = document.querySelector<HTMLElement>(
      '[data-slot="popover-content"]',
    );
    expect(pc, "BgmPicker 的弹层应已展开").toBeTruthy();

    // 弹层同样必须可点（与第一条同源，但走真实组件）
    expect(
      pc!.style.pointerEvents,
      "BgmPicker 弹层未拿到 pointer-events:auto",
    ).toBe("auto");

    const options = Array.from(pc!.querySelectorAll('[role="option"]'));
    expect(options.length, "应渲染出「无」+ 10 首内置共 11 个选项").toBe(11);

    // 滚动区只露约 6.7 行 → 用户报「只有 6 首」。数量徽标是可发现性修复
    expect(
      pc!.textContent ?? "",
      "内置分组标题应带曲目总数（如「内置 10」）",
    ).toMatch(/内置\s*10/);
  });

  it("点击曲目行应真正选中（onChange 收到该曲目 id）", async () => {
    const onChange = vi.fn();
    await act(async () => {
      root.render(
        <Dialog open>
          <DialogContent>
            <BgmPicker
              builtIn={BUILT_IN}
              custom={[]}
              value={null}
              onChange={onChange}
              playingUrl={null}
              onTogglePreview={vi.fn()}
              defaultOpen
            />
          </DialogContent>
        </Dialog>,
      );
    });

    const pc = document.querySelector<HTMLElement>(
      '[data-slot="popover-content"]',
    )!;
    const target = Array.from(
      pc.querySelectorAll<HTMLElement>('[role="option"]'),
    ).find((o) => (o.textContent ?? "").includes("清晨钢琴"));
    expect(target, "应能定位到「清晨钢琴」这一行").toBeTruthy();

    await act(async () => {
      target!.click();
    });

    expect(
      onChange,
      "点击曲目行后 onChange 应收到该曲目 id（能选中，而不只是能看见）",
    ).toHaveBeenCalledWith("morning_piano");
  });
});
