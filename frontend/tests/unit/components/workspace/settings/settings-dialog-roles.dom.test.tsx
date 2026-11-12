import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  isAdmin: false,
  mounts: [] as string[],
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light" }),
}));

vi.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ isAdmin: h.isAdmin }),
}));

vi.mock("@/components/workspace/settings/account-settings-page", () => ({
  AccountSettingsPage: () => {
    h.mounts.push("account");
    return <div data-testid="account-page" />;
  },
}));
vi.mock("@/components/workspace/settings/appearance-settings-page", () => ({
  AppearanceSettingsPage: () => {
    h.mounts.push("appearance");
    return <div data-testid="appearance-page" />;
  },
}));
vi.mock("@/components/workspace/settings/memory-settings-page", () => ({
  MemorySettingsPage: () => {
    h.mounts.push("memory");
    return <div data-testid="memory-page" />;
  },
}));
vi.mock("@/components/workspace/settings/tool-settings-page", () => ({
  ToolSettingsPage: () => {
    h.mounts.push("tools");
    return <div data-testid="tools-page" />;
  },
}));
vi.mock("@/components/workspace/settings/skill-settings-page", () => ({
  SkillSettingsPage: () => {
    h.mounts.push("skills");
    return <div data-testid="skills-page" />;
  },
}));
vi.mock("@/components/workspace/settings/notification-settings-page", () => ({
  NotificationSettingsPage: () => {
    h.mounts.push("notification");
    return <div data-testid="notification-page" />;
  },
}));
vi.mock("@/components/workspace/settings/channel-settings-page", () => ({
  ChannelSettingsPage: () => {
    h.mounts.push("channels");
    return <div data-testid="channels-page" />;
  },
}));
vi.mock("@/components/workspace/settings/about-settings-page", () => ({
  AboutSettingsPage: () => {
    h.mounts.push("about");
    return <div data-testid="about-page" />;
  },
}));

import { SettingsDialog } from "@/components/workspace/settings/settings-dialog";
import { I18nProvider } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("SettingsDialog role gating", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    document.cookie = "locale=en-US; path=/";
    h.isAdmin = false;
    h.mounts = [];
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderDialog(
    defaultSection:
      | "account"
      | "appearance"
      | "memory"
      | "about" = "appearance",
  ) {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="en-US">
          <SettingsDialog open={true} defaultSection={defaultSection} />
        </I18nProvider>,
      );
    });
  }

  function sectionButtons() {
    return [...document.body.querySelectorAll("nav button")].map(
      (button) => button.textContent,
    );
  }

  it("mounts only account and appearance for a regular user and falls back from about", async () => {
    await renderDialog("about");

    expect(sectionButtons()).toEqual([
      enUS.settings.sections.account,
      enUS.settings.sections.appearance,
    ]);
    expect(
      document.body.querySelector("[data-testid='account-page']"),
    ).not.toBeNull();
    expect(
      document.body.querySelector("[data-testid='about-page']"),
    ).toBeNull();
    expect(h.mounts).not.toContain("about");
  });

  it("keeps all eight sections available for an administrator", async () => {
    h.isAdmin = true;
    await renderDialog("about");

    expect(sectionButtons()).toHaveLength(8);
    expect(
      document.body.querySelector("[data-testid='about-page']"),
    ).not.toBeNull();
  });

  it("constrains menu and content in the same frame on admin to user", async () => {
    h.isAdmin = true;
    await renderDialog("memory");
    expect(
      document.body.querySelector("[data-testid='memory-page']"),
    ).not.toBeNull();

    h.isAdmin = false;
    await renderDialog("memory");

    expect(sectionButtons()).toEqual([
      enUS.settings.sections.account,
      enUS.settings.sections.appearance,
    ]);
    expect(
      document.body.querySelector("[data-testid='account-page']"),
    ).not.toBeNull();
    expect(
      document.body.querySelector("[data-testid='memory-page']"),
    ).toBeNull();
  });
});
