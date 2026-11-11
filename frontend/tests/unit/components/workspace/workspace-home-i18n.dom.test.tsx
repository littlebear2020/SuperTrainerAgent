import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkspaceHome from "@/components/workspace/workspace-home";
import { I18nProvider } from "@/core/i18n/context";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));
vi.mock("@/core/threads/hooks", () => ({
  useThreads: () => ({ data: [] }),
}));
vi.mock("@/components/workspace/project-list", () => ({
  ProjectList: () => <div data-testid="project-list" />,
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const CJK = /[一-鿿]/;

describe("workspace-home course entry i18n", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderHome(locale: "en-US" | "zh-CN") {
    document.cookie = `locale=${locale}; path=/`;
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <I18nProvider initialLocale={locale}>
            <WorkspaceHome />
          </I18nProvider>
        </QueryClientProvider>,
      );
    });
  }

  it("en-US home, both course entries, and six cards have no CJK fragments", async () => {
    await renderHome("en-US");
    const text = container.textContent ?? "";
    for (const copy of [
      "What would you like to create?",
      "Video course",
      "Text-based course",
      "Text animation",
      "Image motion",
      "AI video",
      "Recent Projects",
    ]) {
      expect(text).toContain(copy);
    }
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((item) => item.textContent?.includes("Text-based course"))
        ?.click();
    });
    const expandedText = container.textContent ?? "";
    for (const copy of ["Poster design", "PPT creation", "Article writing"]) {
      expect(expandedText).toContain(copy);
    }
    expect(CJK.test(expandedText), `en-US contains CJK: ${expandedText}`).toBe(
      false,
    );
    expect(container.querySelector("input")).toBeNull();
    expect(expandedText).not.toContain("Super agents ready");
  });

  it("zh-CN uses the confirmed home and six entry names", async () => {
    await renderHome("zh-CN");
    const text = container.textContent ?? "";
    for (const copy of [
      "想创造什么？",
      "视频课",
      "图文课",
      "文字动效",
      "图片动效",
      "AI视频",
      "近期项目",
    ]) {
      expect(text).toContain(copy);
    }
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((item) => item.textContent?.includes("图文课"))
        ?.click();
    });
    for (const copy of ["海报制作", "PPT制作", "文章写作"]) {
      expect(container.textContent).toContain(copy);
    }
  });
});
