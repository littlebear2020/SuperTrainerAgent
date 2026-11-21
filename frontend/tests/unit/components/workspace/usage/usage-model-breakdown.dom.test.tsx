import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { UsageModelBreakdown } from "@/components/workspace/usage/usage-model-breakdown";
import { I18nProvider } from "@/core/i18n/context";
import type { UsageModel } from "@/core/usage/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("UsageModelBreakdown", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function render(models: UsageModel[]) {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <UsageModelBreakdown models={models} currency="CNY" />
        </I18nProvider>,
      );
    });
  }

  it("shows every token and cost column, preserving null cache and real zero", async () => {
    await render([
      {
        name: "real-model",
        input_tokens: 60,
        output_tokens: 40,
        cache_read_tokens: null,
        total_tokens: 100,
        cost: 1.5,
        cost_source: "real",
      },
      {
        name: "free-model",
        input_tokens: 0,
        output_tokens: 0,
        cache_read_tokens: 0,
        total_tokens: 0,
        cost: 0,
        cost_source: "estimated",
      },
    ]);

    for (const header of [
      "模型",
      "输入 Token",
      "输出 Token",
      "缓存读取",
      "总 Token",
      "费用",
    ]) {
      expect(container.textContent).toContain(header);
    }
    expect(
      container.querySelector("[data-testid='model-input-0']")?.textContent,
    ).toBe("60");
    expect(
      container.querySelector("[data-testid='model-output-0']")?.textContent,
    ).toBe("40");
    expect(
      container.querySelector("[data-testid='model-cache-0']")?.textContent,
    ).toBe("—");
    expect(
      container.querySelector("[data-testid='model-total-0']")?.textContent,
    ).toBe("100");
    expect(
      container.querySelector("[data-testid='model-cost-0']")?.textContent,
    ).toContain("¥1.50");
    expect(
      container.querySelector("[data-testid='model-cost-source-0']")
        ?.textContent,
    ).toBe("供应商回传");
    expect(
      container.querySelector("[data-testid='model-cache-1']")?.textContent,
    ).toBe("0");
    expect(
      container.querySelector("[data-testid='model-cost-1']")?.textContent,
    ).toContain("¥0.00");
  });

  it("contains long names and huge numbers in its own horizontal scroll area", async () => {
    const longName = `vendor/${"very-long-segment-".repeat(6)}model`;
    await render([
      {
        name: longName,
        input_tokens: 9876543210987,
        output_tokens: 0,
        cache_read_tokens: 0,
        total_tokens: 9876543210987,
        cost: null,
        cost_source: null,
      },
    ]);

    expect(
      container.querySelector("[data-testid='model-name-0']")?.textContent,
    ).toBe(longName);
    expect(
      container.querySelector("[data-testid='model-name-0']")?.className,
    ).toContain("break-all");
    expect(
      container.querySelector("[data-testid='model-total-0']")?.textContent,
    ).toBe("9,876,543,210,987");
    expect(
      container.querySelector("[data-testid='model-cost-0']")?.textContent,
    ).toContain("—");
    expect(
      container.querySelector("[data-testid='model-table-scroll']")?.className,
    ).toContain("overflow-x-auto");
  });

  it("shows the explicit missing-breakdown state", async () => {
    await render([]);
    expect(container.textContent).toContain("暂无模型明细");
    expect(container.querySelector("table")).toBeNull();
  });
});
