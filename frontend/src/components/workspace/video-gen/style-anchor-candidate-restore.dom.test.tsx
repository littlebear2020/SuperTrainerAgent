/**
 * T5（2026-08-31 候选持久化）：StyleAnchorSection 挂载恢复 + 弃用清字段 + 替换文案。
 *
 * - 挂载恢复：candidateName 非空 → 自动进入 create 态并恢复候选条（serve URL 带
 *   ?candidate=）——修「AI 生成风格图退出即丢」三层根因之 1/3。
 * - 恢复态固化走 {candidate: 文件名}（无绝对路径）；「换一张」触发清字段 mutation
 *   （防退出重进复活已弃候选）。
 * - 有锚图时候选条按钮文案 =「替换风格锚图」（与确认框标题一致，T5 可达性）。
 *
 * 沿用 scene-group-card / x11-ux 的 createRoot + act 手写 harness（仓库未装
 * @testing-library/react）。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  setMutate: vi.fn(),
  clearMutate: vi.fn(),
  stubMut: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useGenerateStyleAnchor: h.stubMut,
  useSetStyleAnchor: () => ({ mutate: h.setMutate, isPending: false }),
  useDeleteStyleAnchor: h.stubMut,
  useClearStyleAnchorCandidate: () => ({
    mutate: h.clearMutate,
    isPending: false,
  }),
}));

import { StyleAnchorSection } from "./course-project-detail";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function fireClick(el: Element) {
  act(() => {
    el.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
}

describe("StyleAnchorSection 候选持久化（T5）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.setMutate.mockClear();
    h.clearMutate.mockClear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const render = (props: Partial<Parameters<typeof StyleAnchorSection>[0]>) =>
    act(() =>
      root.render(
        <StyleAnchorSection
          projectId="p1"
          anchor={null}
          relatedTaskCount={0}
          candidateName={null}
          {...props}
        />,
      ),
    );

  it("挂载恢复：candidateName 非空 → create 态候选条 + serve ?candidate= URL", () => {
    render({ candidateName: "1725100000_ab12cd.png" });
    const img = container.querySelector('img[alt="候选风格图"]')!;
    expect(img).toBeTruthy();
    expect(img.getAttribute("src")).toBe(
      "/api/video/projects/p1/style-anchor-image?candidate=1725100000_ab12cd.png",
    );
    expect(container.textContent).toContain("满意这张吗？");
  });

  it("恢复态固化走 {candidate: 文件名}（无绝对路径）", () => {
    render({ candidateName: "c1.png" });
    const btn = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("设为风格锚图"),
    );
    expect(btn).toBeTruthy();
    fireClick(btn!);
    expect(h.setMutate).toHaveBeenCalledWith(
      { candidate: "c1.png" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("换一张 → 清字段 mutation（防退出重进复活已弃候选）+ 候选条消失", () => {
    render({ candidateName: "c1.png" });
    const discard = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("换一张"),
    );
    expect(discard).toBeTruthy();
    fireClick(discard!);
    expect(h.clearMutate).toHaveBeenCalledTimes(1);
    expect(container.querySelector('img[alt="候选风格图"]')).toBeNull();
  });

  it("有锚图时候选条按钮 =「替换风格锚图」（与确认框标题一致）", () => {
    render({ candidateName: "c1.png", anchor: "/abs/anchor.png" });
    const btn = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("风格锚图"),
    );
    expect(btn?.textContent).toContain("替换风格锚图");
    expect(btn?.textContent).not.toContain("设为风格锚图");
    // 点击 → 打开替换确认框（不直接固化）
    fireClick(btn!);
    expect(h.setMutate).not.toHaveBeenCalled();
    const dlg = document.querySelector("[role='alertdialog']");
    expect(dlg?.textContent).toContain("替换风格锚图");
    // z-50 可达性（T5 根因 2 纵深防御）
    expect(dlg?.className).toContain("z-50");
  });

  it("candidateName 空 → 不恢复（空态/锚图态不受影响）", () => {
    render({});
    expect(container.querySelector('img[alt="候选风格图"]')).toBeNull();
    expect(h.clearMutate).not.toHaveBeenCalled();
  });
});
