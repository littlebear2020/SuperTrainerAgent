/**
 * Batch A: inline error states for the five console panels.
 *
 * Each panel must render its own error state (not a top-level banner) so the
 * user sees which panel failed and can retry it independently. The 503/403
 * message mapping lives in `core/console/errors.ts` and is shared.
 *
 * Harness follows `console-honesty.test.tsx`: createRoot + act + happy-dom,
 * real components with only the transport stubbed.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConsolePage } from "@/components/workspace/console/console-page";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { ConsoleApiError } from "@/core/console/api";
import type { ConsoleScope } from "@/core/console/types";
import { I18nProvider } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";

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

const emptyUsage = {
  days: [],
  by_model: {},
  total_tokens: 0,
  total_runs: 0,
  total_cost: null,
  currency: null,
  subagent_tokens: 0,
  scope,
};

const emptyStats = {
  total_runs: 0,
  active_runs: 0,
  failed_runs: 0,
  total_tokens: 0,
  total_cost: null,
  currency: null,
  over_threshold_runs: 0,
  scope,
};

// vi.hoisted ensures the fns exist before the vi.mock factory runs.
const apiFns = vi.hoisted(() => ({
  fetchConsoleStats: vi.fn(),
  fetchConsoleUsage: vi.fn(),
  fetchConsoleRuns: vi.fn(),
  fetchConsoleUsers: vi.fn(),
  fetchConsoleLlmUsage: vi.fn(),
  fetchConsoleExternalCalls: vi.fn(),
  fetchModelPricing: vi.fn(),
  putModelPricing: vi.fn(),
  fetchMediaPricing: vi.fn(async () => ({ pricing: [] })),
  putMediaPricing: vi.fn(),
}));

vi.mock("@/core/console/api", () => ({
  ConsoleApiError: class extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ConsoleApiError";
      this.status = status;
    }
  },
  ...apiFns,
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

describe("Batch A: console panel error states", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    // review-②（2026-09-14）：无 cookie 新访客默认 zh-CN（DSN-003b 拍板 #6）后，
    // 渲染语言不再由 happy-dom 的 navigator 兜底成 en-US——显式钉 cookie 保持 en 断言。
    document.cookie = "locale=en-US; path=/";
    // Default: all succeed with empty data.
    apiFns.fetchConsoleStats.mockResolvedValue(emptyStats);
    apiFns.fetchConsoleUsage.mockResolvedValue(emptyUsage);
    apiFns.fetchConsoleRuns.mockResolvedValue({
      runs: [],
      has_more: false,
      scope,
    });
    apiFns.fetchConsoleUsers.mockResolvedValue({ users: [] });
    apiFns.fetchConsoleLlmUsage.mockResolvedValue({
      domains: [],
      total_cost: null,
      currency: null,
      scope,
    });
    apiFns.fetchConsoleExternalCalls.mockResolvedValue({
      total_calls: 0,
      by_provider: [],
      scope,
    });
    apiFns.fetchModelPricing.mockResolvedValue({ pricing: {} });

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

  const rendered = enUS;

  const renderPage = async (
    systemRole: "user" | "admin" = "user",
  ): Promise<HTMLElement> => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <I18nProvider initialLocale="en-US">
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

  describe("UsageChart error state", () => {
    it("shows inline error on 500, not the empty-state text", async () => {
      apiFns.fetchConsoleUsage.mockRejectedValueOnce(
        new ConsoleApiError(500, "Internal Server Error"),
      );

      const page = await renderPage();

      expect(
        page.querySelector('[data-testid="usage-chart-error"]'),
      ).not.toBeNull();
      // The empty-state text must not appear when there is an error.
      expect(page.textContent).not.toContain(rendered.console.noUsageInWindow);
    });

    it("shows the 503-specific message on 503", async () => {
      apiFns.fetchConsoleUsage.mockRejectedValueOnce(
        new ConsoleApiError(503, "Service Unavailable"),
      );

      const page = await renderPage();

      expect(
        page.querySelector('[data-testid="usage-chart-error"]'),
      ).not.toBeNull();
      expect(page.textContent).toContain(rendered.console.errorNoDatabase);
    });

    it("shows the 403-specific message on 403", async () => {
      apiFns.fetchConsoleUsage.mockRejectedValueOnce(
        new ConsoleApiError(403, "Forbidden"),
      );

      const page = await renderPage();

      expect(page.textContent).toContain(rendered.console.errorForbidden);
    });

    it("shows a retry button that triggers a refetch", async () => {
      apiFns.fetchConsoleUsage
        .mockRejectedValueOnce(new ConsoleApiError(500, "boom"))
        .mockResolvedValueOnce(emptyUsage);

      const page = await renderPage();

      const errorEl = page.querySelector('[data-testid="usage-chart-error"]');
      expect(errorEl).not.toBeNull();

      const retryButton = errorEl!.querySelector("button");
      expect(retryButton).not.toBeNull();
      expect(retryButton!.textContent).toContain(rendered.console.retry);

      await act(async () => {
        retryButton!.click();
      });
      for (let i = 0; i < 5; i++) {
        await act(async () => {
          await new Promise((resolve) => setTimeout(resolve, 0));
        });
      }

      // After retry, the error state should be gone.
      expect(
        page.querySelector('[data-testid="usage-chart-error"]'),
      ).toBeNull();
    });

    it("shows empty-state text on 200 with empty data (regression)", async () => {
      const page = await renderPage();

      expect(
        page.querySelector('[data-testid="usage-chart-error"]'),
      ).toBeNull();
      // The chart replaces {n} with the actual day count — assert the
      // settled text, not the raw template.
      expect(page.textContent).toContain("No conversations or LLM calls");
    });
  });

  describe("StatCards error state", () => {
    it("shows inline error instead of all-zero cards", async () => {
      apiFns.fetchConsoleStats.mockRejectedValueOnce(
        new ConsoleApiError(500, "boom"),
      );

      const page = await renderPage();

      expect(
        page.querySelector('[data-testid="stat-cards-error"]'),
      ).not.toBeNull();
    });
  });

  describe("CompositionPanel empty state (C6b-3 replaces ModelBreakdown)", () => {
    it("shows the empty text when usage has no models", async () => {
      const page = await renderPage();

      // Composition panel's model tab renders the dual-cause empty text
      // (C6/A17②) when the window has no data.
      expect(
        page.querySelector('[data-testid="composition-model"]'),
      ).not.toBeNull();
      expect(page.textContent).toContain(rendered.console.emptyNoData);
    });
  });

  describe("LlmUsagePanel error state", () => {
    it("shows inline error on failure", async () => {
      apiFns.fetchConsoleLlmUsage.mockRejectedValueOnce(
        new ConsoleApiError(500, "boom"),
      );

      const page = await renderPage();

      expect(
        page.querySelector('[data-testid="llm-usage-error"]'),
      ).not.toBeNull();
    });
  });

  describe("ExternalCallsPanel error state", () => {
    it("shows inline error on failure", async () => {
      apiFns.fetchConsoleExternalCalls.mockRejectedValueOnce(
        new ConsoleApiError(500, "boom"),
      );

      const page = await renderPage();

      expect(
        page.querySelector('[data-testid="external-calls-error"]'),
      ).not.toBeNull();
    });
  });

  describe("RunsTable error state", () => {
    it("shows inline error instead of empty table on failure", async () => {
      apiFns.fetchConsoleRuns.mockRejectedValueOnce(
        new ConsoleApiError(500, "boom"),
      );

      const page = await renderPage();

      expect(
        page.querySelector('[data-testid="runs-table-error"]'),
      ).not.toBeNull();
      // The "no runs" empty-state text must not appear when there is an error
      // -- otherwise a failed fetch is indistinguishable from a truly empty
      // table (P2 regression from batch A banner removal).
      expect(page.textContent).not.toContain(rendered.console.noRuns);
    });

    it("shows empty table on 200 with no data (regression)", async () => {
      const page = await renderPage();

      expect(page.querySelector('[data-testid="runs-table-error"]')).toBeNull();
      expect(page.textContent).toContain(rendered.console.noRuns);
    });
  });

  describe("A3: top-level banner removed", () => {
    it("does not render the console-error banner", async () => {
      // Even when stats fails, the banner should not appear - the error is
      // shown inline in the StatCards component.
      apiFns.fetchConsoleStats.mockRejectedValueOnce(
        new ConsoleApiError(500, "boom"),
      );

      const page = await renderPage();

      expect(page.querySelector('[data-testid="console-error"]')).toBeNull();
    });
  });
});
