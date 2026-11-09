/**
 * D1 + D2: the console must not overstate what it is or what it knows.
 *
 * **D1 — role-based naming.** Every user reaches this page, but it means two
 * different things depending on who is looking: a regular user sees only their
 * own usage, an admin sees the platform. Calling it 「运营后台」 for everyone
 * tells a regular user they are looking at platform-wide figures when the API
 * has scoped the response to them. The entry stays visible to all (product
 * decision D4) — only the wording changes.
 *
 * **D2 — cost scope disclosure.** Today the cost figure is honest only by
 * accident: with no prices configured it renders 「—」. Once prices are filled
 * in it becomes a precise-looking number that covers a fraction of real spend,
 * and a precise wrong number is worse than a visible blank. Two limits have to
 * be stated:
 *
 *  1. It covers the chat Agent only — not video, dubbing, writing or image
 *     generation, all of which spend real money elsewhere.
 *  2. The per-model breakdown only covers runs from 2026-08-03 onward. The
 *     `runs.token_usage_by_model` column was created that day, while the
 *     newest earlier run is 2026-07-22, so all ~869 historical rows hold the
 *     column default and never appear in the breakdown at all.
 *
 * Renders the real components with only the transport stubbed, following the
 * createRoot + act + happy-dom harness from `console-admin-gating.test.tsx`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConsolePage } from "@/components/workspace/console/console-page";
import { AuthProvider } from "@/core/auth/AuthProvider";
import type { ConsoleScope } from "@/core/console/types";
import { I18nProvider } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";
import { zhCN } from "@/core/i18n/locales/zh-CN";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/workspace/console",
}));

const scope: ConsoleScope = {
  is_admin: false,
  all_users: false,
  user_id: "u-1",
};

const statsPayload = {
  total_runs: 42,
  active_runs: 1,
  failed_runs: 2,
  total_tokens: 1_500_000,
  total_cost: 12.5,
  currency: "CNY",
  over_threshold_runs: 1,
  scope,
};

const { fetchConsoleStats } = vi.hoisted(() => ({
  fetchConsoleStats: vi.fn(),
}));

vi.mock("@/core/console/api", () => ({
  ConsoleApiError: class extends Error {},
  fetchConsoleStats,
  fetchConsoleUsage: vi.fn(async () => ({
    days: [],
    by_model: {},
    total_tokens: 0,
    total_runs: 0,
    total_cost: null,
    currency: null,
    subagent_tokens: 0,
    scope,
  })),
  fetchConsoleRuns: vi.fn(async () => ({ runs: [], has_more: false, scope })),
  fetchConsoleUsers: vi.fn(async () => ({ users: [] })),
  fetchConsoleLlmUsage: vi.fn(async () => ({
    domains: [],
    total_cost: null,
    currency: null,
    scope,
  })),
  fetchConsoleExternalCalls: vi.fn(async () => ({
    total_calls: 0,
    by_provider: [],
    scope,
  })),
  fetchModelPricing: vi.fn(async () => ({ pricing: {} })),
  putModelPricing: vi.fn(),
  fetchMediaPricing: vi.fn(async () => ({ pricing: [] })),
  putMediaPricing: vi.fn(),
}));

vi.mock("@/core/models/hooks", () => {
  const stableResult = {
    models: [],
    tokenUsageEnabled: false,
    isLoading: false,
    error: null,
  };
  return { useModels: () => stableResult };
});

describe("D1/D2 console honesty", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    // review-②（2026-09-14）：无 cookie 新访客默认 zh-CN（DSN-003b 拍板 #6）后，
    // 渲染语言不再由 happy-dom 的 navigator 兜底成 en-US——显式钉 cookie 保持 en 断言。
    document.cookie = "locale=en-US; path=/";
    fetchConsoleStats.mockResolvedValue(statsPayload);
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
    vi.clearAllMocks();
  });

  /**
   * The rendered locale is whatever `useI18n`'s init effect resolves from the
   * cookie (pinned to en-US in beforeEach) — `initialLocale` is only the
   * pre-hydration seed. Assertions on rendered text use `enUS`; assertions
   * about the *shape* of the copy check both locales.
   */
  const rendered = enUS;

  const renderPage = async (
    systemRole: "user" | "admin",
    locale: "zh-CN" | "en-US" = "zh-CN",
  ): Promise<HTMLElement> => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <I18nProvider initialLocale={locale}>
            <AuthProvider
              initialUser={{
                id: "u-1",
                email: "a@example.com",
                system_role: systemRole,
                needs_setup: false,
              }}
            >
              <ConsolePage />
            </AuthProvider>
          </I18nProvider>
        </QueryClientProvider>,
      );
    });

    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }

    const el = container.firstElementChild as HTMLElement | null;
    if (!el) throw new Error("ConsolePage rendered nothing");
    return el;
  };

  describe("D1: the page names itself for its audience", () => {
    it("calls itself 我的用量 for a regular user", async () => {
      const page = await renderPage("user");
      const heading = page.querySelector("header h1");

      expect(heading?.textContent).toBe(rendered.console.titleSelf);
      expect(heading?.textContent).not.toBe(rendered.console.titleAdmin);
    });

    it("calls itself 运营后台 for an admin", async () => {
      const page = await renderPage("admin");
      const heading = page.querySelector("header h1");

      expect(heading?.textContent).toBe(rendered.console.titleAdmin);
    });

    it("keeps the two titles distinct in both locales", () => {
      // A shared string would silently defeat the whole task.
      expect(zhCN.console.titleSelf).not.toBe(zhCN.console.titleAdmin);
      expect(enUS.console.titleSelf).not.toBe(enUS.console.titleAdmin);
      expect(zhCN.sidebar.consoleSelf).not.toBe(zhCN.sidebar.consoleAdmin);
      expect(enUS.sidebar.consoleSelf).not.toBe(enUS.sidebar.consoleAdmin);
    });
  });

  describe("D2: the cost card states what it does not cover", () => {
    it("discloses the LLM/media split on the full-chain cost card (C3)", async () => {
      const page = await renderPage("user");

      // The card is full-chain now (C3); the honest disclosure is the
      // breakdown itself — LLM and media must both be named next to the
      // headline figure so nobody reconciles against half a total.
      expect(page.textContent).toContain(rendered.console.costBreakdownLlm);
      expect(page.textContent).toContain(rendered.console.costBreakdownMedia);
    });

    it("discloses the date from which per-model attribution exists", async () => {
      const page = await renderPage("user");

      expect(page.textContent).toContain(rendered.console.compositionScopeNote);
      // The boundary date must actually appear — a vague "recent runs only"
      // would leave the reader unable to tell which runs are missing.
      expect(zhCN.console.compositionScopeNote).toContain("2026-08-03");
      expect(enUS.console.compositionScopeNote).toContain("2026-08-03");
    });

    it("names the excluded modules rather than hedging", async () => {
      // "partial data" tells the reader nothing actionable; the point is to
      // say which spend is missing so nobody reconciles against a wrong total.
      for (const keyword of ["视频", "配音", "写作"]) {
        expect(zhCN.console.usagePanelNote).toContain(keyword);
      }
      for (const keyword of ["video", "dubbing", "writing"]) {
        expect(enUS.console.usagePanelNote.toLowerCase()).toContain(keyword);
      }
    });
  });

  describe("D2: unpriced hint matches formatCost's own null handling", () => {
    it("says 'unpriced' when cost is null", async () => {
      fetchConsoleStats.mockResolvedValue({
        ...statsPayload,
        total_cost: null,
        currency: null,
      });
      const page = await renderPage("user");

      expect(page.textContent).toContain(rendered.console.costUnpriced);
    });

    it("says 'unpriced' when cost is undefined too", async () => {
      // formatCost treats null and undefined identically (both render 「—」),
      // but the hint only checked `=== null`. An undefined total therefore
      // rendered 「—」 next to a currency label, implying a real zero-ish
      // figure in that currency rather than "no prices configured".
      fetchConsoleStats.mockResolvedValue({
        ...statsPayload,
        total_cost: undefined,
        currency: "CNY",
      });
      const page = await renderPage("user");

      expect(page.textContent).toContain(rendered.console.costUnpriced);
    });

    it("shows the cost figure, not the unpriced hint, when a cost exists", async () => {
      const page = await renderPage("user");

      expect(page.textContent).not.toContain(rendered.console.costUnpriced);
      // statsPayload has total_cost 12.5 (no total_cost_all → legacy
      // fallback); the formatted figure must render.
      expect(page.textContent).toContain("12.50");
    });
  });
});
