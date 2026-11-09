/**
 * D1, sidebar half: the nav entry names itself for its audience.
 *
 * The page title was covered by `console-honesty.test.tsx`, but the sidebar
 * label was not — and that gap was not theoretical. Hardcoding the label to
 * `t.sidebar.consoleAdmin` (i.e. every regular user sees 「运营后台」, exactly
 * the bug D1 exists to prevent) left the entire 740-test suite green. The
 * existing coverage only asserted that the two locale strings *differ*, which
 * says nothing about which one the component picks.
 *
 * So this renders the real `WorkspaceNavChatList` under both roles. It needs a
 * `SidebarProvider`: `useSidebar()` throws outside one.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider } from "@/components/ui/sidebar";
import { WorkspaceNavChatList } from "@/components/workspace/workspace-nav-chat-list";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { I18nProvider } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/workspace/chats",
}));

describe("D1 sidebar: console entry label follows the caller's role", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 })),
    );
    // review-②（2026-09-14）：无 cookie 新访客默认 zh-CN（DSN-003b 拍板 #6）后，
    // 渲染语言不再由 happy-dom 的 navigator 兜底成 en-US——显式钉 cookie 保持 en 断言。
    document.cookie = "locale=en-US; path=/";
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

  /**
   * Rendered locale is resolved by `useI18n`'s mount effect from the cookie
   * (pinned to en-US in beforeEach), not from `initialLocale` — so DOM
   * assertions read from `enUS`.
   */
  const renderNav = async (systemRole: "user" | "admin"): Promise<string> => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <I18nProvider initialLocale="zh-CN">
            <AuthProvider
              initialUser={{
                id: "u-1",
                email: "a@example.com",
                system_role: systemRole,
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

    const link = container.querySelector('a[href="/workspace/console"]');
    if (!link) throw new Error("console nav entry did not render");
    return link.textContent ?? "";
  };

  it("labels the entry 'My usage' for a regular user", async () => {
    const label = await renderNav("user");

    expect(label).toContain(enUS.sidebar.consoleSelf);
    expect(label).not.toContain(enUS.sidebar.consoleAdmin);
  });

  it("labels the entry 'Operations console' for an admin", async () => {
    const label = await renderNav("admin");

    expect(label).toContain(enUS.sidebar.consoleAdmin);
  });

  it("still shows the entry to a regular user", async () => {
    // Product decision D4: everyone keeps the entry; only the wording differs.
    // A test that only checked the label would pass if the entry vanished.
    await renderNav("user");

    expect(
      container.querySelector('a[href="/workspace/console"]'),
    ).not.toBeNull();
  });
});
