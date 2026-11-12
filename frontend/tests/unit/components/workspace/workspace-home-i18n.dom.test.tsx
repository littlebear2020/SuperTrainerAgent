import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkspaceHome from "@/components/workspace/workspace-home";
import { I18nProvider } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";
import { zhCN } from "@/core/i18n/locales/zh-CN";

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
const CAPABILITY_IDS = [
  "course-video",
  "ai_image",
  "ai_video",
  "creative-studio",
  "ppt-master",
  "podcast-studio",
] as const;

describe("workspace-home six-capability i18n", () => {
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

  it("shows the exact English hero and all six cards without CJK or recent projects", async () => {
    await renderHome("en-US");
    const text = container.textContent ?? "";
    expect(text).toContain("Great content starts with one idea.");
    expect(text).toContain(
      "Choose a tool to turn your text, images, and source material into videos, posters, slides, or podcasts.",
    );
    for (const id of CAPABILITY_IDS) {
      expect(text).toContain(enUS.workspace.home.courseItems[id].title);
      expect(text).toContain(enUS.workspace.home.courseItems[id].description);
    }
    expect(text).not.toContain("Recent Projects");
    expect(text).not.toContain("Visual & audio course");
    expect(CJK.test(text), `en-US contains CJK: ${text}`).toBe(false);
  });

  it("shows the exact Chinese hero and all six descriptions without recent projects", async () => {
    await renderHome("zh-CN");
    const text = container.textContent ?? "";
    expect(text).toContain("让好内容，从一个想法开始");
    expect(text).toContain(
      "选择一种能力，把文字、图片和资料变成视频、海报、PPT 或播客。",
    );
    for (const id of CAPABILITY_IDS) {
      expect(text).toContain(zhCN.workspace.home.courseItems[id].title);
      expect(text).toContain(zhCN.workspace.home.courseItems[id].description);
    }
    expect(text).not.toContain("近期项目");
    expect(text).not.toContain("图文音频课");
  });
});
