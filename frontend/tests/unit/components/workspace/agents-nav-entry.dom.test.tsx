import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider } from "@/components/ui/sidebar";
import { WorkspaceHeader } from "@/components/workspace/workspace-header";
import { WorkspaceNavChatList } from "@/components/workspace/workspace-nav-chat-list";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { I18nProvider } from "@/core/i18n/context";
import {
  PALETTE_NAV_ITEMS,
  WORKSPACE_NAV_ITEMS,
} from "@/core/workspace/nav-routes";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/workspace/agents/course-assistant/chats/new",
}));

describe("侧边栏可见入口收敛", () => {
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
    await act(async () => root.unmount());
    container.remove();
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  async function renderNav() {
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
                <WorkspaceHeader />
                <WorkspaceNavChatList />
              </SidebarProvider>
            </AuthProvider>
          </I18nProvider>
        </QueryClientProvider>,
      );
    });
  }

  it("only video assets and console remain in route navigation", async () => {
    await renderNav();
    expect(WORKSPACE_NAV_ITEMS.map((item) => item.href)).toEqual([
      "/workspace/video-gen",
      "/workspace/console",
    ]);
    for (const href of ["/workspace/video-gen", "/workspace/console"]) {
      expect(container.querySelector(`a[href="${href}"]`)).not.toBeNull();
    }
    for (const href of [
      "/workspace/chats",
      "/workspace/agents",
      "/workspace/gallery",
      "/workspace/twins/voice",
      "/workspace/writer",
      "/workspace/assets",
    ]) {
      expect(container.querySelector(`a[href="${href}"]`)).toBeNull();
    }
  });

  it("home keeps exact active matching and Chinese label", async () => {
    await renderNav();
    const home = container.querySelector('a[href="/workspace"]');
    expect(home?.textContent).toBe("首页");
    expect(home?.getAttribute("data-active")).not.toBe("true");
  });

  it("palette follows the same visible page set", () => {
    expect(PALETTE_NAV_ITEMS.map((item) => item.href)).toEqual([
      "/workspace/video-gen",
      "/workspace/console",
    ]);
  });
});
