/**
 * C6/C6b redesign pins（2026-08-27 成本可信化批次）。
 *
 * 页级断言：默认 7 天（A17）、统计卡随 days（A5）、总账卡读 total_cost_all
 * （C3）、四卡瘦身（A7）、用量/花费切换卡、构成区三 tab、一级 tab 角色视图
 * （定价管理仅 admin）、新鲜度条、外部调用面板后端 media_type 过滤 + 过滤后
 * 合计、单价修正分界标注。
 *
 * Harness同 console-honesty.test.tsx：createRoot + act + happy-dom，仅打桩传输层。
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

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/workspace/console",
}));

const scope: ConsoleScope = {
  is_admin: true,
  all_users: true,
  user_id: null,
};

const statsPayload = {
  total_runs: 42,
  active_runs: 1,
  failed_runs: 2,
  total_tokens: 1_500_000,
  total_cost: 12.5, // legacy LLM-only (chat)
  total_cost_all: 30.9, // full chain: LLM 25 + media 5.9
  cost_breakdown: { llm: 25.0, media: 5.9 },
  unpriced_calls: 3,
  currency: "CNY",
  over_threshold_runs: 1,
  scope,
};

const usagePayload = {
  days: [
    {
      date: "2026-08-26",
      total_tokens: 1000,
      chat_tokens: 600,
      llm_tokens: 400,
      runs: 2,
      cost: 1.2,
    },
  ],
  by_model: {
    "vendor/m": {
      runs: 2,
      tokens: 1000,
      input_tokens: 800,
      output_tokens: 200,
      cache_read_tokens: 0,
      cost: 1.2,
    },
  },
  total_tokens: 1000,
  total_runs: 2,
  total_cost: 1.2,
  total_cost_all: 7.1,
  cost_breakdown: { llm: 1.2, media: 5.9 },
  unpriced_calls: 3,
  media_cost_by_provider: { seedance: 5.9 },
  media_cost_by_provider_split: { seedance: { real: 1.0, estimated: 4.9 } },
  currency: "CNY",
  subagent_tokens: 0,
  scope,
};

const externalCallsPayload = {
  total_calls: 30,
  by_provider: [
    {
      provider: "seedance",
      calls: 10,
      cost_real: 1.0,
      cost_estimated: 4.9,
      cost: 5.9,
      priced: true,
      unit: "per_kilo_token",
      media_type: "video",
    },
    {
      provider: "gpt-image-2",
      calls: 20,
      cost_real: null,
      cost_estimated: 0.8,
      cost: 0.8,
      priced: true,
      unit: "per_kilo_token",
      media_type: "image",
    },
  ],
  by_date: [
    { date: "2026-08-26", calls: 30, cost_real: 1.0, cost_estimated: 5.7 },
  ],
  by_type: [
    { media_type: "video", calls: 10, cost: 5.9 },
    { media_type: "image", calls: 20, cost: 0.8 },
  ],
  unpriced_calls: 3,
  scope,
};

const apiFns = vi.hoisted(() => ({
  fetchConsoleStats: vi.fn(),
  fetchConsoleUsage: vi.fn(),
  fetchConsoleRuns: vi.fn(),
  fetchConsoleUsers: vi.fn(),
  fetchConsoleLlmUsage: vi.fn(),
  fetchConsoleExternalCalls: vi.fn(),
  fetchModelPricing: vi.fn(),
  putModelPricing: vi.fn(),
  fetchMediaPricing: vi.fn(),
  putMediaPricing: vi.fn(),
  patchMediaPricing: vi.fn(),
  deleteMediaPricing: vi.fn(),
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

vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({
    models: [],
    tokenUsageEnabled: false,
    isLoading: false,
    error: null,
  }),
}));

describe("C6/C6b console redesign", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  const rendered = enUS;

  beforeEach(() => {
    // review-②（2026-09-14）：无 cookie 新访客默认 zh-CN（DSN-003b 拍板 #6）后，
    // 渲染语言不再由 happy-dom 的 navigator 兜底成 en-US——显式钉 cookie 保持 en 断言。
    document.cookie = "locale=en-US; path=/";
    vi.clearAllMocks();
    apiFns.fetchConsoleStats.mockResolvedValue(statsPayload);
    apiFns.fetchConsoleUsage.mockResolvedValue(usagePayload);
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
    apiFns.fetchConsoleExternalCalls.mockResolvedValue(externalCallsPayload);
    apiFns.fetchModelPricing.mockResolvedValue({ pricing: {} });
    apiFns.fetchMediaPricing.mockResolvedValue({ pricing: [] });

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
  });

  const renderPage = async (
    systemRole: "user" | "admin" = "admin",
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

  it("defaults to a 7-day window and windows the stats call (A17/A5)", async () => {
    await renderPage();
    expect(apiFns.fetchConsoleStats).toHaveBeenCalledWith(undefined, 7);
    expect(apiFns.fetchConsoleUsage).toHaveBeenCalledWith(
      expect.objectContaining({ days: 7 }),
    );
  });

  it("stat cards are the A7 four: full-chain cost / tokens / runs / unpriced", async () => {
    const page = await renderPage();
    const cards = page.querySelectorAll('[data-testid^="stat-card-"]');
    expect(cards.length).toBe(4);
    expect(
      page.querySelector('[data-testid="stat-card-cost-all"]'),
    ).not.toBeNull();
    expect(
      page.querySelector('[data-testid="stat-card-tokens"]'),
    ).not.toBeNull();
    expect(page.querySelector('[data-testid="stat-card-runs"]')).not.toBeNull();
    expect(
      page.querySelector('[data-testid="stat-card-unpriced"]'),
    ).not.toBeNull();
    // C3: the headline figure is total_cost_all (30.9), not legacy total_cost
    expect(page.textContent).toContain("30.90");
  });

  it("failed/threshold counts moved to the runs panel headline (C6b-2)", async () => {
    const page = await renderPage();
    const headline = page.querySelector('[data-testid="runs-panel-headline"]');
    expect(headline).not.toBeNull();
    expect(headline!.textContent).toContain("2"); // failed_runs
    expect(headline!.textContent).toContain("1"); // over_threshold_runs
  });

  it("chart card switches between usage and cost with the boundary note (C6)", async () => {
    const page = await renderPage();
    // Default tab = usage; cost chart absent, note absent
    expect(
      page.querySelector('[data-testid="chart-tab-usage"]'),
    ).not.toBeNull();
    expect(page.querySelector('[data-testid="console-cost-chart"]')).toBeNull();
    expect(page.textContent).not.toContain(
      rendered.console.priceFixBoundaryNote,
    );

    const costTab = page.querySelector('[data-testid="chart-tab-cost"]')!;
    await act(async () => {
      (costTab as HTMLElement).click();
    });
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
    expect(
      page.querySelector('[data-testid="console-cost-chart"]'),
    ).not.toBeNull();
    // 单价修正分界标注在花费 tab 可见
    expect(page.textContent).toContain(rendered.console.priceFixBoundaryNote);
  });

  it("composition panel exposes model/provider/type tabs (C6b-3)", async () => {
    const page = await renderPage();
    expect(
      page.querySelector('[data-testid="composition-tab-model"]'),
    ).not.toBeNull();
    expect(
      page.querySelector('[data-testid="composition-tab-provider"]'),
    ).not.toBeNull();
    expect(
      page.querySelector('[data-testid="composition-tab-type"]'),
    ).not.toBeNull();

    // Provider tab renders the C4 real/estimated split rows
    const providerTab = page.querySelector(
      '[data-testid="composition-tab-provider"]',
    )!;
    await act(async () => {
      (providerTab as HTMLElement).click();
    });
    const providerView = page.querySelector(
      '[data-testid="composition-provider"]',
    );
    expect(providerView).not.toBeNull();
    expect(providerView!.textContent).toContain("5.90");
  });

  it("admin sees the pricing tab (C6b-4)", async () => {
    const adminPage = await renderPage("admin");
    expect(
      adminPage.querySelector('[data-testid="top-tab-pricing"]'),
    ).not.toBeNull();
  });

  it("regular user never sees the pricing tab (C6b-4)", async () => {
    // Separate test from the admin one: AuthProvider seeds its user state on
    // first mount, so re-rendering the same root with a different
    // initialUser would silently keep the admin identity.
    const userPage = await renderPage("user");
    expect(
      userPage.querySelector('[data-testid="top-tab-pricing"]'),
    ).toBeNull();
    expect(userPage.textContent).not.toContain(
      rendered.console.tabPricingAdmin,
    );
  });

  it("freshness bar and manual refresh exist (C6b-5)", async () => {
    const page = await renderPage();
    expect(
      page.querySelector('[data-testid="console-freshness"]'),
    ).not.toBeNull();
    expect(
      page.querySelector('[data-testid="console-refresh"]'),
    ).not.toBeNull();
  });

  it("external-calls filters by backend media_type and syncs totals (C6)", async () => {
    const page = await renderPage();
    // Unfiltered: totals = 30 calls
    const panel = page.querySelector('[data-testid="external-calls-panel"]')!;
    const tfootCalls = () =>
      panel.querySelector("tfoot td:nth-child(2)")!.textContent;

    expect(tfootCalls()).toContain("30");

    const imageTab = page.querySelector(
      '[data-testid="external-calls-tab-image"]',
    )!;
    await act(async () => {
      (imageTab as HTMLElement).click();
    });
    // image = gpt-image-2 only (backend media_type, not a frontend Set)
    expect(tfootCalls()).toContain("20");

    // B1（2026-08-27 评审修复）后双类型 provider 出两行、各归各 tab——
    // 这里的两个 mock provider 均为单类型，验证精确过滤与过滤集合计。
    const videoTab = page.querySelector(
      '[data-testid="external-calls-tab-video"]',
    )!;
    await act(async () => {
      (videoTab as HTMLElement).click();
    });
    expect(tfootCalls()).toContain("10");
  });

  it("estimated values carry the * marker and the legend names it (C4/C6)", async () => {
    const page = await renderPage();
    const panel = page.querySelector('[data-testid="external-calls-panel"]')!;
    // gpt-image-2 (estimate-only row) renders the * marker; providerLabel
    // localizes the name, so locate rows by the marker + unpriced text.
    const markerCells = Array.from(
      panel.querySelectorAll("tbody tr td:nth-child(3)"),
    );
    expect(markerCells.some((td) => td.textContent?.includes("*"))).toBe(true);
    expect(panel.textContent).toContain(rendered.console.providerCostNote);
  });

  it("double-type provider yields per-type rows with correct per-tab totals (B1)", async () => {
    // B1（2026-08-27 评审修复）：qihu 同名双类型（image+video）→ by_provider
    // 两行；各 tab 只见本类型行，合计只含本类型——修前折叠单值导致一边整行
    // 消失且合计漏算。
    apiFns.fetchConsoleExternalCalls.mockResolvedValueOnce({
      total_calls: 3,
      by_provider: [
        {
          provider: "qihu",
          calls: 2,
          cost_real: 3.0,
          cost_estimated: null,
          cost: 3.0,
          priced: true,
          unit: "per_kilo_token",
          media_type: "video",
        },
        {
          provider: "qihu",
          calls: 1,
          cost_real: 0.5,
          cost_estimated: null,
          cost: 0.5,
          priced: true,
          unit: "per_image",
          media_type: "image",
        },
      ],
      by_date: [
        { date: "2026-08-26", calls: 3, cost_real: 3.5, cost_estimated: 0 },
      ],
      by_type: [
        { media_type: "video", calls: 2, cost: 3.0 },
        { media_type: "image", calls: 1, cost: 0.5 },
      ],
      unpriced_calls: 0,
      scope,
    });
    const page = await renderPage();
    const panel = page.querySelector('[data-testid="external-calls-panel"]')!;
    const tfootCalls = () =>
      panel.querySelector("tfoot td:nth-child(2)")!.textContent;
    const tfootCost = () =>
      panel.querySelector("tfoot td:nth-child(3)")!.textContent;

    // all tab：两行都可见（同名两行各带类型后缀），合计 3 次 / ¥3.50
    expect(panel.querySelectorAll("tbody tr").length).toBe(2);
    expect(tfootCalls()).toContain("3");
    expect(tfootCost()).toContain("3.50");

    // video tab：只 video 行，合计 2 次 / ¥3.00
    const videoTab = page.querySelector(
      '[data-testid="external-calls-tab-video"]',
    )!;
    await act(async () => {
      (videoTab as HTMLElement).click();
    });
    expect(panel.querySelectorAll("tbody tr").length).toBe(1);
    expect(tfootCalls()).toContain("2");
    expect(tfootCost()).toContain("3.00");

    // image tab：只 image 行，合计 1 次 / ¥0.50（修前此处 qihu 整行消失、合计漏算）
    const imageTab = page.querySelector(
      '[data-testid="external-calls-tab-image"]',
    )!;
    await act(async () => {
      (imageTab as HTMLElement).click();
    });
    expect(panel.querySelectorAll("tbody tr").length).toBe(1);
    expect(tfootCalls()).toContain("1");
    expect(tfootCost()).toContain("0.50");
  });

  it("trend card note follows the chart tab (F1)", async () => {
    // F1（2026-08-27 评审修复）：花费 tab 的口径说明必须是媒体口径
    // （costPanelNote），修前固定显示 usagePanelNote（"不含媒体生成"）与图表相反。
    const page = await renderPage();
    const note = page.querySelector('[data-testid="chart-panel-note"]')!;
    expect(note.textContent).toBe(rendered.console.usagePanelNote);

    const costTab = page.querySelector('[data-testid="chart-tab-cost"]')!;
    await act(async () => {
      (costTab as HTMLElement).click();
    });
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
    expect(
      page.querySelector('[data-testid="chart-panel-note"]')!.textContent,
    ).toBe(rendered.console.costPanelNote);
  });

  it("runs-panel headline annotates the stat window (F2)", async () => {
    // F2（2026-08-27 评审修复）：头部小字是窗口口径（默认 7 天）、表格是
    // 全时段分页——头部必须标注窗口。
    const page = await renderPage();
    const headline = page.querySelector('[data-testid="runs-panel-headline"]')!;
    expect(headline.textContent).toContain(
      rendered.console.runsPanelWindowSuffix.replace("{days}", "7"),
    );
  });

  it("cost-all card breakdown carries the currency symbol (F5)", async () => {
    // F5（2026-08-27 评审修复）：拆分行与主数值同币种——修前传 null 币种，
    // 主值 "¥30.90" 对小字裸数字。
    const page = await renderPage();
    const card = page.querySelector('[data-testid="stat-card-cost-all"]')!;
    expect(card.textContent).toContain("¥30.90");
    expect(card.textContent).toContain("¥25.00");
    expect(card.textContent).toContain("¥5.90");
  });
});
