/**
 * DEF-3（2026-10-07）：工作台首页 hero 文案必须走 i18n。
 * 修前 workspace-home.tsx 硬编码中文——cookie=en-US 时首屏中文残片。
 *
 * 范围口径：WorkflowGrid / ProjectList 各有自己的硬编码中文（计划 §4 的
 * 127 文件清单内、本批不修），故 mock 掉子组件，断言精确覆盖本组件自有
 * 文案（eyebrow / h1 / 副标题 / 输入框 placeholder / 分区标题与 viewAll）。
 */

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
vi.mock("@/components/workspace/workflow-grid", () => ({
  WorkflowGrid: () => <div data-testid="workflow-grid" />,
}));
vi.mock("@/components/workspace/project-list", () => ({
  ProjectList: () => <div data-testid="project-list" />,
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const CJK = /[一-鿿]/;

describe("DEF-3: workspace-home 首屏文案 locale 化", () => {
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

  async function renderHome(locale: "en-US" | "zh-CN") {
    // DSN-003b：无 cookie 访客一律回落 zh-CN——en 断言必须显式钉 cookie
    // （同 console-nav-label.test.tsx 头注的既定约定）。
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

  it("en-US 态本组件自有文案零中文残片", async () => {
    await renderHome("en-US");

    const text = container.textContent ?? "";
    expect(text).toContain("What would you like to create?");
    expect(text).toContain("Super agents ready");
    expect(text).toContain("Expert Agents");
    expect(text).toContain("Recent Projects");
    const placeholder = container.querySelector("input")?.placeholder ?? "";
    expect(placeholder).toBe(
      "Describe what you want to create, or ask anything...",
    );

    // DEF-3 核心断言：组件自有渲染文本无 CJK 残片
    expect(CJK.test(text), `en-US 态仍有中文残片: ${text}`).toBe(false);
    expect(CJK.test(placeholder)).toBe(false);
  });

  it("zh-CN 态文案不回退英文（键真实存在）", async () => {
    await renderHome("zh-CN");

    const text = container.textContent ?? "";
    expect(text).toContain("想创造什么？");
    expect(text).toContain("专家Agent");
    expect(text).toContain("近期项目");
    expect(text).toContain("超级智能体已就绪");
  });
});
