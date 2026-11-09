/**
 * D2（2026-08-19）：SceneGroupCard 受控展开 + DOM 锚点。
 * 修前展开态是内部 useState（defaultExpanded 仅首次挂载生效），进度条点击
 * 只改 activeGroup 无法展开目标组；也无任何滚动锚点。
 * 仓库未装 @testing-library/react，沿用 createRoot + act 手写渲染模式。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  SceneGroupData,
  StoryboardFrameData,
} from "@/core/video-gen/types";

import { SceneGroupCard } from "./scene-group-card";

const group: SceneGroupData = {
  index: 0,
  title: "导语",
  frame_indices: [0],
};
const frames = [{ index: 0, narration: "n0" }] as StoryboardFrameData[];

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function fireClick(el: Element) {
  act(() => {
    el.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
}

describe("SceneGroupCard 受控展开（D2）", () => {
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
  });

  it("受控模式点击标题回调 onOpenChange", async () => {
    const onOpenChange = vi.fn();
    await act(async () => {
      root.render(
        <SceneGroupCard
          group={group}
          frames={frames}
          videoType="ai_image"
          renderFrameCard={() => <div key="c" />}
          open={false}
          onOpenChange={onOpenChange}
          anchorId="scene-group-0"
        />,
      );
    });
    const title = [...container.querySelectorAll("span")].find(
      (s) => s.textContent === "导语",
    );
    expect(title).toBeTruthy();
    fireClick(title!);
    // open=false 受控：点击标题请求展开 → 回调目标态 true
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });

  it("非受控回落：defaultExpanded 初始展开，点击标题切内部态收起（D2 回归）", async () => {
    // D2 修前坑：展开态是内部 useState、defaultExpanded 仅首次挂载生效，
    // 进度条点击无法程序化展开。修复 = 受控 open/onOpenChange（上一用例）；
    // 本用例锁回落路径——不传受控 props 时内部态仍完整工作（detail 页兼容）。
    await act(async () => {
      root.render(
        <SceneGroupCard
          group={group}
          frames={frames}
          videoType="ai_image"
          renderFrameCard={(f) => <div key={f.index}>帧卡{f.index}</div>}
          defaultExpanded
          anchorId="scene-group-0"
        />,
      );
    });
    // defaultExpanded=true → CollapsibleContent 首挂载即展开
    expect(container.textContent).toContain("帧卡0");

    const title = [...container.querySelectorAll("span")].find(
      (s) => s.textContent === "导语",
    );
    expect(title).toBeTruthy();
    fireClick(title!);
    // 内部态收起：内容卸载、trigger 回 closed 态
    expect(container.textContent).not.toContain("帧卡0");
    expect(
      container.querySelector("button[data-state]")?.getAttribute("data-state"),
    ).toBe("closed");
  });

  it("根元素带 anchorId", async () => {
    await act(async () => {
      root.render(
        <SceneGroupCard
          group={group}
          frames={frames}
          videoType="ai_image"
          renderFrameCard={() => <div key="c" />}
          anchorId="scene-group-0"
        />,
      );
    });
    expect(container.querySelector("#scene-group-0")).not.toBeNull();
  });
});
