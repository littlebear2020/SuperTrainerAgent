import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ isAdmin: false, pricingMounts: 0 }));

vi.mock("@/core/console/hooks", () => ({
  useIsConsoleAdmin: () => h.isAdmin,
  useConsoleStats: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    dataUpdatedAt: 0,
    refetch: vi.fn(),
  })),
  useConsoleUsage: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    dataUpdatedAt: 0,
    refetch: vi.fn(),
  })),
  useConsoleExternalCalls: vi.fn(() => ({
    data: { by_date: [] },
    isLoading: false,
    refetch: vi.fn(),
  })),
  useConsoleRuns: vi.fn(() => ({
    data: { runs: [], has_more: false },
    isLoading: false,
    refetch: vi.fn(),
  })),
  useModelPricing: vi.fn(),
  useMediaPricing: vi.fn(),
}));
vi.mock("@/core/usage/hooks", () => ({
  usePersonalUsage: vi.fn(() => ({
    data: {
      window: { days: 7, start_date: "2026-11-15", end_date: "2026-11-21" },
      summary: {
        cost: 1.5,
        currency: "CNY",
        cost_source: "real",
        total_tokens: 100,
      },
      tasks: [],
      total_tasks: 0,
      has_more: false,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })),
}));
vi.mock("@/components/workspace/console/console-pricing-panel", () => ({
  ConsolePricingPanel: () => {
    h.pricingMounts += 1;
    return <div data-testid="pricing-panel">定价管理</div>;
  },
}));
vi.mock("@/components/workspace/console/console-stat-cards", () => ({
  ConsoleStatCards: () => <div data-testid="admin-analysis">管理员运营台</div>,
}));
vi.mock("@/components/workspace/console/console-composition-panel", () => ({
  ConsoleCompositionPanel: () => null,
}));
vi.mock("@/components/workspace/console/console-cost-chart", () => ({
  ConsoleCostChart: () => null,
}));
vi.mock("@/components/workspace/console/console-external-calls-panel", () => ({
  ConsoleExternalCallsPanel: () => null,
}));
vi.mock("@/components/workspace/console/console-llm-usage-panel", () => ({
  ConsoleLlmUsagePanel: () => null,
}));
vi.mock("@/components/workspace/console/console-runs-table", () => ({
  ConsoleRunsTable: () => null,
}));
vi.mock("@/components/workspace/console/console-usage-chart", () => ({
  ConsoleUsageChart: () => null,
}));
vi.mock("@/components/workspace/console/console-user-picker", () => ({
  ALL_USERS_VALUE: "all",
  ConsoleUserPicker: () => null,
}));

import { ConsoleRolePage } from "@/components/workspace/console/console-role-page";
import {
  useConsoleExternalCalls,
  useConsoleRuns,
  useConsoleStats,
  useConsoleUsage,
  useMediaPricing,
  useModelPricing,
} from "@/core/console/hooks";
import { I18nProvider } from "@/core/i18n/context";
import { usePersonalUsage } from "@/core/usage/hooks";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("ConsoleRolePage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    document.cookie = "locale=en-US; path=/";
    h.isAdmin = false;
    h.pricingMounts = 0;
    vi.clearAllMocks();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderPage() {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="en-US">
          <ConsoleRolePage />
        </I18nProvider>,
      );
    });
  }

  it("mounts only personal usage and no legacy console or pricing queries for regular users", async () => {
    await renderPage();

    expect(
      container.querySelector("[data-testid='personal-usage-page']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='admin-analysis']"),
    ).toBeNull();
    expect(usePersonalUsage).toHaveBeenCalled();
    expect(useConsoleStats).not.toHaveBeenCalled();
    expect(useConsoleUsage).not.toHaveBeenCalled();
    expect(useConsoleExternalCalls).not.toHaveBeenCalled();
    expect(useConsoleRuns).not.toHaveBeenCalled();
    expect(useModelPricing).not.toHaveBeenCalled();
    expect(useMediaPricing).not.toHaveBeenCalled();
  });

  it("keeps admin on the legacy page and unmounts pricing and old queries on admin-to-user", async () => {
    h.isAdmin = true;
    await renderPage();
    expect(
      container.querySelector("[data-testid='admin-analysis']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='personal-usage-page']"),
    ).toBeNull();
    expect(usePersonalUsage).not.toHaveBeenCalled();
    expect(useConsoleStats).toHaveBeenCalledTimes(1);
    expect(useConsoleUsage).toHaveBeenCalledTimes(1);
    expect(useConsoleExternalCalls).toHaveBeenCalledTimes(1);
    expect(useConsoleRuns).toHaveBeenCalledTimes(1);

    await act(async () => {
      container
        .querySelector("[data-testid='top-tab-pricing']")!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(
      container.querySelector("[data-testid='pricing-panel']"),
    ).not.toBeNull();
    expect(h.pricingMounts).toBe(1);
    const legacyCallCounts = [
      vi.mocked(useConsoleStats).mock.calls.length,
      vi.mocked(useConsoleUsage).mock.calls.length,
      vi.mocked(useConsoleExternalCalls).mock.calls.length,
      vi.mocked(useConsoleRuns).mock.calls.length,
    ];

    h.isAdmin = false;
    await renderPage();

    expect(container.querySelector("[data-testid='pricing-panel']")).toBeNull();
    expect(
      container.querySelector("[data-testid='admin-analysis']"),
    ).toBeNull();
    expect(
      container.querySelector("[data-testid='personal-usage-page']"),
    ).not.toBeNull();
    expect(h.pricingMounts).toBe(1);
    expect(useConsoleStats).toHaveBeenCalledTimes(legacyCallCounts[0]!);
    expect(useConsoleUsage).toHaveBeenCalledTimes(legacyCallCounts[1]!);
    expect(useConsoleExternalCalls).toHaveBeenCalledTimes(legacyCallCounts[2]!);
    expect(useConsoleRuns).toHaveBeenCalledTimes(legacyCallCounts[3]!);
    expect(usePersonalUsage).toHaveBeenCalled();
  });
});
