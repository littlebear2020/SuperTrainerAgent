import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  isAdmin: true,
  pricingMounts: 0,
}));

vi.mock("@/core/console/hooks", () => ({
  useIsConsoleAdmin: () => h.isAdmin,
  useConsoleStats: () => ({
    data: undefined,
    isLoading: false,
    dataUpdatedAt: 0,
    refetch: vi.fn(),
  }),
  useConsoleUsage: () => ({
    data: undefined,
    isLoading: false,
    dataUpdatedAt: 0,
    refetch: vi.fn(),
  }),
  useConsoleExternalCalls: () => ({
    data: { by_date: [] },
    isLoading: false,
    refetch: vi.fn(),
  }),
  useConsoleRuns: () => ({
    data: { runs: [], has_more: false },
    isLoading: false,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/components/workspace/console/console-pricing-panel", () => ({
  ConsolePricingPanel: () => {
    h.pricingMounts += 1;
    return <div data-testid="pricing-panel" />;
  },
}));
vi.mock("@/components/workspace/console/console-stat-cards", () => ({
  ConsoleStatCards: () => <div data-testid="analysis-panel" />,
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

import { ConsolePage } from "@/components/workspace/console/console-page";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("ConsolePage role transition", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    document.cookie = "locale=en-US; path=/";
    h.isAdmin = true;
    h.pricingMounts = 0;
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
          <ConsolePage />
        </I18nProvider>,
      );
    });
  }

  it("returns pricing to analysis in the same frame on admin to user", async () => {
    await renderPage();
    await act(async () => {
      container
        .querySelector("[data-testid='top-tab-pricing']")!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(
      container.querySelector("[data-testid='pricing-panel']"),
    ).not.toBeNull();

    h.isAdmin = false;
    await renderPage();

    expect(container.querySelector("[data-testid='pricing-panel']")).toBeNull();
    expect(
      container.querySelector("[data-testid='analysis-panel']"),
    ).not.toBeNull();
    expect(
      container.querySelector("[data-testid='top-tab-pricing']"),
    ).toBeNull();
    expect(
      container.querySelector("[data-testid='top-tab-analysis']")!.className,
    ).toContain("bg-primary");
    expect(h.pricingMounts).toBe(1);
  });
});
