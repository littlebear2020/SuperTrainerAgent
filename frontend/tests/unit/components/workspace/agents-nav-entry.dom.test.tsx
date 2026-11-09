/**
 * 2026-09-22 工作台恢复：侧边栏「智能体」入口加回（b68f3a7e4 反向），经
 * nav-routes.ts 单一事实源接入。⌘K 面板维持 09-14 拍板 6 页口径——agents
 * 不设 includeInPalette，由下方守卫断言固化该隔离（防误入）。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider } from "@/components/ui/sidebar";
import { WorkspaceNavChatList } from "@/components/workspace/workspace-nav-chat-list";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { I18nProvider } from "@/core/i18n/context";
import { PALETTE_NAV_ITEMS } from "@/core/workspace/nav-routes";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/workspace/agents/course-assistant/chats/new",
}));

describe("侧边栏：智能体入口已恢复", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 })),
    );
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  const renderNav = async (): Promise<void> => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <I18nProvider initialLocale="zh-CN">
            <AuthProvider
              initialUser={{
                id: "u-1",
                email: "a@example.com",
                system_role: "user",
                needs_setup: false,
              }}
            >
              <SidebarProvider>
                <WorkspaceNavChatList />
              </SidebarProvider>
            </AuthProvider>
          </I18nProvider>
        </QueryClientProvider>,
      );
    });
  };

  it("渲染指向 /workspace/agents 的链接", async () => {
    await renderNav();

    expect(
      container.querySelector('a[href="/workspace/agents"]'),
    ).not.toBeNull();
  });

  it("其余导航入口仍然渲染", async () => {
    await renderNav();

    expect(
      container.querySelector('a[href="/workspace/chats"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('a[href="/workspace/console"]'),
    ).not.toBeNull();
  });
});

describe("⌘K 面板隔离（DSN-002 六页口径）", () => {
  it("PALETTE_NAV_ITEMS 不含 agents 入口", () => {
    expect(PALETTE_NAV_ITEMS.map((item) => item.href)).not.toContain(
      "/workspace/agents",
    );
  });
});
