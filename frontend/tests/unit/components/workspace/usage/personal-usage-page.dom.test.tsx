import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PersonalUsagePage } from "@/components/workspace/usage/personal-usage-page";
import { I18nProvider } from "@/core/i18n/context";
import type { FetchPersonalUsageOptions } from "@/core/usage/api";
import type { PersonalUsageResponse, UsageTask } from "@/core/usage/types";

const h = vi.hoisted(() => ({
  result: null as unknown,
  calls: [] as unknown[],
  refetch: vi.fn(),
}));

vi.mock("@/core/usage/hooks", () => ({
  usePersonalUsage: (options: unknown) => {
    h.calls.push(options);
    return h.result;
  },
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function task(status: UsageTask["status"], id: string = status): UsageTask {
  return {
    task_key: `agent:${id}`,
    source_id: id,
    capability: "course-video",
    title: `${id}任务`,
    status,
    cost: 1.5,
    llm_cost: 1.5,
    media_cost: null,
    currency: "CNY",
    cost_source: "real",
    total_tokens: 100,
    models: [
      {
        name: "real-model",
        input_tokens: 60,
        output_tokens: 40,
        cache_read_tokens: null,
        total_tokens: 100,
        cost: 1.5,
        cost_source: "real",
      },
    ],
    updated_at: "2026-11-21T13:00:00Z",
  };
}

function response(
  tasks: UsageTask[],
  overrides: Partial<PersonalUsageResponse> = {},
): PersonalUsageResponse {
  return {
    window: { days: 7, start_date: "2026-11-15", end_date: "2026-11-21" },
    summary: {
      cost: 1.5,
      currency: "CNY",
      cost_source: "real",
      total_tokens: 100,
    },
    tasks,
    total_tasks: tasks.length,
    has_more: false,
    ...overrides,
  };
}

function success(data: PersonalUsageResponse) {
  return {
    data,
    isLoading: false,
    isError: false,
    error: null,
    refetch: h.refetch,
  };
}

describe("PersonalUsagePage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.calls = [];
    h.refetch = vi.fn();
    h.result = success(response([task("completed")]));
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
        <I18nProvider initialLocale="zh-CN">
          <PersonalUsagePage />
        </I18nProvider>,
      );
    });
  }

  async function click(selector: string) {
    await act(async () => {
      container
        .querySelector(selector)!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  }

  function latestOptions(): FetchPersonalUsageOptions {
    return h.calls.at(-1) as FetchPersonalUsageOptions;
  }

  it("renders two totals, only three windows, six capabilities, and exact task states", async () => {
    h.result = success(
      response([task("completed"), task("running"), task("incomplete")]),
    );
    await renderPage();

    expect(
      container.querySelector("[data-testid='usage-summary-cost']")
        ?.textContent,
    ).toContain("已核算费用");
    expect(
      container.querySelector("[data-testid='usage-summary-cost']")
        ?.textContent,
    ).toContain("¥1.50");
    expect(
      container.querySelector("[data-testid='usage-summary-tokens']")
        ?.textContent,
    ).toContain("总 Token");
    expect(
      container.querySelector("[data-testid='usage-summary-tokens']")
        ?.textContent,
    ).toContain("100");
    expect(
      container.querySelectorAll("[data-testid^='usage-days-']"),
    ).toHaveLength(3);
    expect(container.querySelector("[data-testid='usage-days-90']")).toBeNull();
    expect(
      container.querySelectorAll("[data-testid^='usage-capability-']"),
    ).toHaveLength(7);
    expect(container.textContent).toContain("全部");
    for (const label of [
      "文字动效",
      "图片动效",
      "AI视频",
      "课程海报",
      "PPT课件",
      "语音播客",
    ]) {
      expect(container.textContent).toContain(label);
    }
    expect(
      container.querySelector("[data-testid='task-status-completed']")
        ?.textContent,
    ).toBe("已完成");
    expect(
      container.querySelector("[data-testid='task-status-running']")
        ?.textContent,
    ).toBe("进行中");
    expect(
      container.querySelector("[data-testid='task-status-incomplete']")
        ?.textContent,
    ).toBe("未完成");
  });

  it("resets pagination and expanded rows when days or capability changes", async () => {
    h.result = success(
      response([task("completed")], { total_tasks: 21, has_more: true }),
    );
    await renderPage();
    await click("[data-testid='usage-next-page']");
    expect(latestOptions().offset).toBe(20);

    await act(async () => {
      container
        .querySelector("[data-testid='task-row-completed']")!
        .dispatchEvent(
          new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
        );
    });
    expect(
      container.querySelector("[data-testid='task-details-completed']"),
    ).not.toBeNull();

    h.result = success(
      response([task("completed")], {
        window: { days: 14, start_date: "2026-11-08", end_date: "2026-11-21" },
        summary: {
          cost: 3,
          currency: "CNY",
          cost_source: "real",
          total_tokens: 200,
        },
      }),
    );
    await click("[data-testid='usage-days-14']");
    expect(latestOptions()).toMatchObject({ days: 14, offset: 0 });
    expect(
      container.querySelector("[data-testid='task-details-completed']"),
    ).toBeNull();
    expect(
      container.querySelector("[data-testid='usage-summary-cost']")
        ?.textContent,
    ).toContain("¥3.00");
    expect(
      container.querySelector("[data-testid='usage-summary-tokens']")
        ?.textContent,
    ).toContain("200");

    await act(async () => {
      container
        .querySelector("[data-testid='task-row-completed']")!
        .dispatchEvent(
          new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
        );
    });
    h.result = success(
      response([task("completed", "image")], {
        summary: {
          cost: 0.5,
          currency: "CNY",
          cost_source: "estimated",
          total_tokens: 50,
        },
      }),
    );
    await click("[data-testid='usage-capability-ai_image']");
    expect(latestOptions()).toMatchObject({
      capability: "ai_image",
      offset: 0,
    });
    expect(
      container.querySelector("[data-testid='task-details-image']"),
    ).toBeNull();
    expect(
      container.querySelector("[data-testid='usage-summary-cost']")
        ?.textContent,
    ).toContain("¥0.5000");
  });

  it("shows loading, a retryable error without fake zeros, a unified empty state, and short-page counts", async () => {
    h.result = {
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: h.refetch,
    };
    await renderPage();
    expect(container.textContent).toContain("正在加载用量");

    h.result = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("offline"),
      refetch: h.refetch,
    };
    await renderPage();
    expect(container.textContent).toContain("用量加载失败");
    expect(container.textContent).not.toContain("¥0.00");
    await click("[data-testid='usage-retry']");
    expect(h.refetch).toHaveBeenCalledTimes(1);

    h.result = success(
      response([], {
        summary: {
          cost: null,
          currency: null,
          cost_source: null,
          total_tokens: null,
        },
      }),
    );
    await renderPage();
    expect(container.textContent).toContain("所选条件下暂无用量记录");
    expect(
      container.querySelector("[data-testid='usage-summary-cost']")
        ?.textContent,
    ).toContain("—");

    h.result = success(
      response([task("completed"), task("running")], {
        total_tasks: 2,
        has_more: false,
      }),
    );
    await renderPage();
    expect(
      container.querySelector("[data-testid='usage-page-info']")?.textContent,
    ).toContain("1–2 / 2");
    expect(
      container
        .querySelector("[data-testid='usage-next-page']")
        ?.hasAttribute("disabled"),
    ).toBe(true);
  });

  it("marks same-user placeholder data as updating while keeping it visible", async () => {
    h.result = {
      ...success(response([task("completed")])),
      isFetching: true,
      isPlaceholderData: true,
    };
    await renderPage();

    expect(
      container.querySelector("[data-testid='usage-summary-cost']")
        ?.textContent,
    ).toContain("¥1.50");
    expect(
      container.querySelector("[data-testid='usage-updating']")?.textContent,
    ).toContain("正在加载用量");
  });

  it("groups the window control in the header and all capabilities in one toolbar", async () => {
    await renderPage();

    const header = container.querySelector("[data-testid='usage-header']");
    expect(header?.textContent).toContain("我的用量");
    const windowControl = header?.querySelector(
      "[data-testid='usage-window-control']",
    );
    expect(windowControl).not.toBeNull();
    expect(windowControl?.querySelectorAll("button")).toHaveLength(3);

    const toolbar = container.querySelector(
      "[data-testid='usage-filter-toolbar']",
    );
    expect(toolbar?.textContent).toContain("按能力筛选");
    expect(
      toolbar?.querySelectorAll("[data-testid='usage-filter-scroll']"),
    ).toHaveLength(1);
    expect(toolbar?.querySelectorAll("button")).toHaveLength(7);
    expect(
      container.querySelectorAll("[data-testid^='usage-capability-']"),
    ).toHaveLength(7);

    for (const button of container.querySelectorAll(
      "[data-testid='usage-header'] button, [data-testid='usage-filter-toolbar'] button",
    )) {
      expect(button.className).toContain("focus-visible:ring-indigo-500");
    }
  });

  it("keeps both existing totals inside one responsive summary panel", async () => {
    h.result = success(response([task("completed")]));
    await renderPage();

    const panel = container.querySelector("[data-testid='usage-summary']");
    expect(panel).not.toBeNull();
    const items = Array.from(panel!.children).filter((child) =>
      child.hasAttribute("data-testid"),
    );
    expect(items.map((item) => item.getAttribute("data-testid"))).toEqual([
      "usage-summary-cost",
      "usage-summary-tokens",
    ]);
    for (const item of items) {
      expect(item.tagName).toBe("SECTION");
      expect(
        item.querySelector(`[id='${item.getAttribute("aria-labelledby")}']`),
      ).not.toBeNull();
      const icon = item.querySelector("svg");
      expect(icon).not.toBeNull();
      expect(icon!.closest("[aria-hidden='true']")).not.toBeNull();
    }
    expect(items[0].textContent).toContain("已核算费用");
    expect(items[0].textContent).toContain("¥1.50");
    expect(items[0].textContent).toContain("含估算；未知不记为 0");
    expect(
      items[0].querySelector("[data-testid='usage-summary-cost-source']")
        ?.textContent,
    ).toBe("供应商回传");
    expect(items[1].textContent).toContain("总 Token");
    expect(items[1].textContent).toContain("100");
    expect(items[1].textContent).toContain("输入 + 输出，缓存读取不重复累加");

    h.result = success(
      response([], {
        summary: {
          cost: null,
          currency: null,
          cost_source: null,
          total_tokens: null,
        },
      }),
    );
    await renderPage();
    expect(
      container.querySelector("[data-testid='usage-summary-cost']")
        ?.textContent,
    ).toContain("—");
    expect(
      container.querySelector("[data-testid='usage-summary-tokens']")
        ?.textContent,
    ).toContain("—");
    expect(
      container.querySelector("[data-testid='usage-summary-cost-source']"),
    ).toBeNull();

    h.result = success(
      response([], {
        summary: {
          cost: 0,
          currency: "CNY",
          cost_source: "real",
          total_tokens: 0,
        },
      }),
    );
    await renderPage();
    expect(
      container.querySelector("[data-testid='usage-summary-cost']")
        ?.textContent,
    ).toContain("¥0.00");
    expect(
      container.querySelector("[data-testid='usage-summary-tokens']")
        ?.textContent,
    ).toContain("0");
  });
});
