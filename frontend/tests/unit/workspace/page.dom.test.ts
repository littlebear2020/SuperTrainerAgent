/**
 * 2026-09-22 工作台恢复：/workspace 首页直接渲染 WorkspaceHome（九宫格
 * 工作台），不再 307 重定向到 course-assistant 新会话页（3e44f5389 反向）。
 * 本文件保持 .ts 扩展名（计划口径），故用 createElement 而非 JSX。
 */
import { redirect } from "next/navigation";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkspacePage from "@/app/workspace/page";

vi.mock("next/navigation", () => ({
  redirect: vi.fn(),
}));

vi.mock("@/components/workspace/workspace-home", async () => {
  const { createElement } = await import("react");
  return {
    default: () =>
      createElement(
        "div",
        { "data-testid": "workspace-home-stub" },
        "WorkspaceHome",
      ),
  };
});

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("/workspace 入口 — 渲染工作台首页", () => {
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

  it("渲染 WorkspaceHome 而非重定向", async () => {
    await act(async () => {
      root.render(createElement(WorkspacePage));
    });

    expect(
      container.querySelector('[data-testid="workspace-home-stub"]'),
    ).not.toBeNull();
    expect(redirect).not.toHaveBeenCalled();
  });
});
