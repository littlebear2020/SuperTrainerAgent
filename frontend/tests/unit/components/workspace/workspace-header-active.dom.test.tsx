/**
 * 2026-09-22 工作台恢复：「工作台」入口高亮回退精确匹配（37bb1563e 反向）——
 * 仅 /workspace 本身高亮；agent 聊天页归「智能体」入口（prefix 匹配），
 * 防止恢复后聊天页误亮工作台（BUG-001 双高亮红线相邻项）。
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider } from "@/components/ui/sidebar";
import { WorkspaceHeader } from "@/components/workspace/workspace-header";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let mockPathname = "/workspace";
let mockTheme = "light";

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: mockTheme }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => mockPathname,
}));

describe("侧边栏「工作台」入口高亮", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    mockTheme = "light";
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

  const renderHeader = async (
    collapsed = false,
  ): Promise<HTMLAnchorElement | null> => {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <SidebarProvider defaultOpen={!collapsed}>
            <WorkspaceHeader />
          </SidebarProvider>
        </I18nProvider>,
      );
    });
    return container.querySelector<HTMLAnchorElement>('a[href="/workspace"]');
  };

  it("在 /workspace 首页时处于激活态", async () => {
    mockPathname = "/workspace";
    const link = await renderHeader();

    expect(link).not.toBeNull();
    expect(link?.getAttribute("data-active")).toBe("true");
  });

  it("使用 Trainer AI 品牌图标", async () => {
    mockPathname = "/workspace";
    await renderHeader();

    expect(
      container.querySelector("img[src='/images/trainer-ai-mark.svg']"),
    ).not.toBeNull();
  });

  it("深色模式在展开和收起状态都使用反白品牌图标", async () => {
    mockTheme = "dark";
    await renderHeader();
    expect(
      container.querySelector("img[src='/images/trainer-ai-mark-inverse.svg']"),
    ).not.toBeNull();
    await renderHeader(true);
    expect(
      container.querySelector("img[src='/images/trainer-ai-mark-inverse.svg']"),
    ).not.toBeNull();
  });

  it("在 course-assistant 聊天页时不激活（归「智能体」入口）", async () => {
    mockPathname = "/workspace/agents/course-assistant/chats/new";
    const link = await renderHeader();

    expect(link).not.toBeNull();
    expect(link?.getAttribute("data-active")).not.toBe("true");
  });
});
