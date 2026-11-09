/**
 * C7: console-cost-chart component tests.
 *
 * Verifies the chart renders bars for daily data and handles zero/empty data.
 */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ConsoleCostChart } from "@/components/workspace/console/console-cost-chart";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function Wrap({ children }: { children: ReactNode }) {
  return <I18nProvider initialLocale="zh-CN">{children}</I18nProvider>;
}

describe("C7 console-cost-chart", () => {
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

  it("renders the empty-state text (not the chart) for empty data (C6)", async () => {
    await act(async () => {
      root.render(
        <Wrap>
          <ConsoleCostChart data={[]} currency="CNY" />
        </Wrap>,
      );
    });
    expect(
      container.querySelector('[data-testid="console-cost-chart"]'),
    ).toBeNull();
    expect(
      container.querySelector('[data-testid="cost-chart-empty"]'),
    ).not.toBeNull();
  });

  it("renders the dual-bar legend and uses the backend currency (C6)", async () => {
    const data = [
      { date: "2026-08-26", calls: 2, cost_real: 1.0, cost_estimated: 0.5 },
    ];
    await act(async () => {
      root.render(
        <Wrap>
          <ConsoleCostChart data={data} currency="CNY" />
        </Wrap>,
      );
    });
    expect(
      container.querySelector('[data-testid="cost-chart-legend"]'),
    ).not.toBeNull();
    // The symbol must come from the backend-provided currency prop (C6 deleted
    // the hardcoded ¥): CNY → ¥ in the bar tooltip (1.0 + 0.5 = ¥1.50).
    const barCny = container.querySelector(
      '[data-testid="console-cost-chart"] .group',
    );
    expect(barCny?.getAttribute("title")).toContain("¥1.50");

    // Same data under a different backend currency → the symbol follows.
    await act(async () => {
      root.render(
        <Wrap>
          <ConsoleCostChart data={data} currency="USD" />
        </Wrap>,
      );
    });
    const barUsd = container.querySelector(
      '[data-testid="console-cost-chart"] .group',
    );
    expect(barUsd?.getAttribute("title")).toContain("$1.50");
    expect(barUsd?.getAttribute("title")).not.toContain("¥");
  });

  it("renders bars for each day", async () => {
    const data = [
      { date: "2026-08-10", calls: 5, cost_real: 1.5, cost_estimated: 0.5 },
      { date: "2026-08-11", calls: 3, cost_real: 0, cost_estimated: 2.0 },
      { date: "2026-08-12", calls: 0, cost_real: 0, cost_estimated: 0 },
    ];
    await act(async () => {
      root.render(
        <Wrap>
          <ConsoleCostChart data={data} currency="CNY" />
        </Wrap>,
      );
    });
    const chart = container.querySelector('[data-testid="console-cost-chart"]');
    expect(chart).not.toBeNull();
    // Each day is a bar (group relative div)
    const bars = chart!.querySelectorAll(".group");
    expect(bars.length).toBe(3);
  });

  it("handles all-zero data without crashing", async () => {
    const data = [
      { date: "2026-08-10", calls: 0, cost_real: 0, cost_estimated: 0 },
    ];
    await act(async () => {
      root.render(
        <Wrap>
          <ConsoleCostChart data={data} currency="CNY" />
        </Wrap>,
      );
    });
    const chart = container.querySelector('[data-testid="console-cost-chart"]');
    expect(chart).not.toBeNull();
  });
});
