/**
 * B2 + B3: stacked bar rendering and empty-state text.
 *
 * B2: the chart renders a stacked bar (chat + llm) when the backend provides
 * chat_tokens/llm_tokens. Old backends fall back to a single-color bar.
 * B3: the empty-state text includes the {n} day count interpolation.
 *
 * Harness follows `console-honesty.test.tsx`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConsoleUsageChart } from "@/components/workspace/console/console-usage-chart";
import { AuthProvider } from "@/core/auth/AuthProvider";
import type { ConsoleUsage } from "@/core/console/types";
import { I18nProvider } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/workspace/console",
}));

vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({
    models: [],
    tokenUsageEnabled: false,
    isLoading: false,
    error: null,
  }),
}));

const scope = { is_admin: false, all_users: false, user_id: "u-1" };

function buildUsage(overrides: Partial<ConsoleUsage> = {}): ConsoleUsage {
  return {
    days: [],
    by_model: {},
    total_tokens: 0,
    total_runs: 0,
    total_cost: null,
    currency: null,
    subagent_tokens: 0,
    scope,
    ...overrides,
  };
}

describe("B2+B3: console usage chart", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
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
    vi.clearAllMocks();
  });

  const rendered = enUS;

  const renderChart = async (
    usage: ConsoleUsage | undefined,
  ): Promise<HTMLElement> => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <I18nProvider initialLocale="en-US">
            <AuthProvider
              initialUser={{
                id: "u-1",
                email: "a@example.com",
                system_role: "user",
                needs_setup: false,
              }}
            >
              <ConsoleUsageChart usage={usage} isLoading={false} />
            </AuthProvider>
          </I18nProvider>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const el = container.firstElementChild as HTMLElement | null;
    if (!el) throw new Error("Chart rendered nothing");
    return el;
  };

  describe("B3: empty-state text with {n} interpolation", () => {
    it("includes the day count in the empty-state message", async () => {
      const usage = buildUsage({
        days: Array.from({ length: 14 }, (_, i) => ({
          date: `2026-08-${String(i + 1).padStart(2, "0")}`,
          total_tokens: 0,
          input_tokens: 0,
          output_tokens: 0,
          runs: 0,
          cost: null,
        })),
      });

      const page = await renderChart(usage);

      // The {n} placeholder must be replaced with "14".
      expect(page.textContent).toContain("14");
      expect(page.textContent).not.toContain("{n}");
      // The guiding text must be present.
      expect(page.textContent).toContain("Chats");
      expect(page.textContent).toContain("Video");
    });
  });

  describe("B2: stacked bar rendering", () => {
    it("renders legend labels when there is data", async () => {
      const usage = buildUsage({
        days: [
          {
            date: "2026-08-11",
            total_tokens: 1000,
            input_tokens: 600,
            output_tokens: 400,
            runs: 1,
            chat_tokens: 600,
            llm_tokens: 400,
            llm_calls: 2,
            cost: null,
          },
        ],
        total_tokens: 1000,
        total_runs: 1,
      });

      const page = await renderChart(usage);

      expect(page.textContent).toContain(rendered.console.legendChat);
      expect(page.textContent).toContain(rendered.console.legendLlm);
    });

    it("renders trendScopeNote under the title", async () => {
      const usage = buildUsage({
        days: [
          {
            date: "2026-08-11",
            total_tokens: 100,
            input_tokens: 60,
            output_tokens: 40,
            runs: 1,
            cost: null,
          },
        ],
        total_tokens: 100,
        total_runs: 1,
      });

      const page = await renderChart(usage);

      expect(page.textContent).toContain(rendered.console.trendScopeNote);
    });

    it("falls back to total_tokens when chat_tokens is absent (old backend)", async () => {
      const usage = buildUsage({
        days: [
          {
            date: "2026-08-11",
            total_tokens: 500,
            input_tokens: 300,
            output_tokens: 200,
            runs: 1,
            cost: null,
          },
        ],
        total_tokens: 500,
        total_runs: 1,
      });

      const page = await renderChart(usage);

      // The bar should still render (data-testid exists).
      const bar = page.querySelector('[data-testid="usage-bar-2026-08-11"]');
      expect(bar).not.toBeNull();
      // data-tokens should have the total.
      expect(bar!.getAttribute("data-tokens")).toBe("500");
    });

    it("does not render legend when peak is 0", async () => {
      const usage = buildUsage({
        days: Array.from({ length: 7 }, (_, i) => ({
          date: `2026-08-${String(i + 1).padStart(2, "0")}`,
          total_tokens: 0,
          input_tokens: 0,
          output_tokens: 0,
          runs: 0,
          cost: null,
        })),
      });

      const page = await renderChart(usage);

      // The legend color swatches have `size-2.5` class; they should not
      // exist when peak is 0. (Checking text "Chat" would false-positive
      // on the empty-state message "Start a task in Chats, ...")
      expect(page.querySelectorAll(".size-2\\.5")).toHaveLength(0);
    });
  });
});
