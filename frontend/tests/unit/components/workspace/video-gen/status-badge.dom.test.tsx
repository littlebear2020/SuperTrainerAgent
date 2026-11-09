/**
 * 共享 StatusBadge 契约（2026-10-06 合并批 + Q6 拍板）。
 *
 * Q6：draft≥100 的「就绪待编辑」态对齐列表页——amber + Pencil、不转圈
 * （修前详情页私有副本无此分支，落入 indigo + Loader2 转圈的「进行中」误导）。
 * 本测试钉死统一后语义（列表/详情共用一份实现）。
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { StatusBadge } from "@/components/workspace/video-gen/video-gen-page";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("StatusBadge", () => {
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

  async function renderBadge(status: string, progress?: number) {
    await act(async () => {
      root.render(<StatusBadge status={status} progress={progress} />);
    });
    return container.querySelector("span")!;
  }

  it("draft ≥100：amber + Pencil、不转圈（Q6「待编辑」语义）", async () => {
    const el = await renderBadge("draft", 100);
    expect(el.className).toContain("bg-amber-100");
    expect(el.className).not.toContain("bg-indigo-100");
    expect(el.querySelector("svg")?.classList.contains("animate-spin")).toBe(
      false,
    );
    // 文案仍走 getVideoTaskStatusLabel（draft→「草稿」）；Q6 只统一视觉语义
    expect(el.textContent).toContain("草稿");
  });

  it("draft <100：indigo 转圈（生成中语义）", async () => {
    const el = await renderBadge("draft", 40);
    expect(el.className).toContain("bg-indigo-100");
    expect(el.querySelector("svg")?.classList.contains("animate-spin")).toBe(
      true,
    );
  });

  it("ready：green 不转圈；failed：red 不转圈", async () => {
    const ready = await renderBadge("ready");
    expect(ready.className).toContain("bg-green-100");
    expect(ready.querySelector("svg")?.classList.contains("animate-spin")).toBe(
      false,
    );
    const failed = await renderBadge("failed");
    expect(failed.className).toContain("bg-red-100");
    expect(
      failed.querySelector("svg")?.classList.contains("animate-spin"),
    ).toBe(false);
  });

  it("composing：indigo 转圈 + E16 文案「重新合成中」", async () => {
    const el = await renderBadge("composing");
    expect(el.className).toContain("bg-indigo-100");
    expect(el.textContent).toContain("重新合成中");
  });
});
