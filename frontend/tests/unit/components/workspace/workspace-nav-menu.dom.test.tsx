import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  settingsProps: null as Record<string, unknown> | null,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/workspace",
}));

vi.mock("@/components/workspace/settings", () => ({
  SettingsDialog: (props: Record<string, unknown>) => {
    h.settingsProps = props;
    return <div data-testid="settings-dialog" />;
  },
}));

import { SidebarProvider } from "@/components/ui/sidebar";
import { WorkspaceNavMenu } from "@/components/workspace/workspace-nav-menu";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { I18nProvider } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("WorkspaceNavMenu real user footer", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 })),
    );
    document.cookie = "locale=en-US; path=/";
    h.settingsProps = null;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  async function renderFooter(systemRole: "user" | "admin") {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="en-US">
          <AuthProvider
            initialUser={{
              id: "u-1",
              email: "alice@example.com",
              system_role: systemRole,
              needs_setup: false,
            }}
          >
            <SidebarProvider>
              <WorkspaceNavMenu />
            </SidebarProvider>
          </AuthProvider>
        </I18nProvider>,
      );
    });
  }

  it("shows the real email-derived identity and an independent settings gear", async () => {
    await renderFooter("user");

    expect(container.textContent).toContain("alice");
    expect(container.textContent).not.toContain("alice@example.com");
    expect(
      container.querySelector("[title='alice@example.com']"),
    ).not.toBeNull();
    expect(container.textContent).toContain("A");
    expect(container.textContent).toContain(enUS.sidebar.roleUser);
    expect(container.textContent).not.toContain("Pro Plan");
    expect(container.textContent).not.toContain("Settings and more");
    expect(container.querySelectorAll("button")).toHaveLength(1);
    expect(container.querySelector("button button")).toBeNull();

    await act(async () => container.querySelector("button")!.click());
    expect(h.settingsProps).toMatchObject({
      open: true,
      defaultSection: "appearance",
    });
  });

  it("shows a non-empty fallback and avatar initial for a null user", async () => {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="en-US">
          <AuthProvider initialUser={null}>
            <SidebarProvider>
              <WorkspaceNavMenu />
            </SidebarProvider>
          </AuthProvider>
        </I18nProvider>,
      );
    });

    expect(container.textContent).toContain("guest");
    expect(container.querySelector("[title='guest']")).not.toBeNull();
    expect(container.textContent).toContain("?");
  });

  it("shows the administrator role without changing the identity", async () => {
    await renderFooter("admin");

    expect(container.textContent).toContain("alice");
    expect(container.textContent).toContain(enUS.sidebar.roleAdmin);
    expect(container.textContent).not.toContain("Pro Plan");
  });
});
