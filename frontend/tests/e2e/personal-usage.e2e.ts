import {
  expect,
  test,
  type Locator,
  type Page,
  type Route,
} from "@playwright/test";

import type {
  PersonalUsageResponse,
  UsageCostSource,
  UsageDays,
  UsageModel,
  UsageTask,
} from "../../src/core/usage/types";

import REGULAR_USER from "./utils/personal-usage-user.json" with { type: "json" };

const APP_URL = `http://localhost:${process.env.E2E_PORT ?? 3127}`;

function model(
  name: string,
  input: number,
  output: number,
  cache: number | null,
  cost: number | null,
  source: UsageCostSource,
): UsageModel {
  return {
    name,
    input_tokens: input,
    output_tokens: output,
    cache_read_tokens: cache,
    total_tokens: input + output,
    cost,
    cost_source: source,
  };
}

function task(
  id: string,
  capability: UsageTask["capability"],
  overrides: Partial<UsageTask> = {},
): UsageTask {
  const isVideoTask = capability === "ai_image" || capability === "ai_video";
  const taskKeyPrefix = isVideoTask ? "video" : "agent";
  return {
    task_key: `${taskKeyPrefix}:${id}`,
    source_id: id,
    capability,
    title: id,
    status: "completed",
    cost: 1,
    llm_cost: 1,
    media_cost: null,
    currency: "CNY",
    cost_source: "real",
    total_tokens: 10,
    models: [],
    updated_at: "2026-11-21T13:00:00Z",
    ...overrides,
  };
}

function summary(
  cost: number | null,
  tokens: number | null,
  source: UsageCostSource,
): PersonalUsageResponse["summary"] {
  return {
    cost,
    currency: cost === null ? null : "CNY",
    cost_source: source,
    total_tokens: tokens,
  };
}

const START_DATE_BY_DAYS: Record<UsageDays, string> = {
  7: "2026-11-15",
  14: "2026-11-08",
  30: "2026-10-23",
};

function response(
  days: UsageDays,
  tasks: UsageTask[],
  usageSummary: PersonalUsageResponse["summary"],
  totalTasks = tasks.length,
  hasMore = false,
): PersonalUsageResponse {
  return {
    window: {
      days,
      start_date: START_DATE_BY_DAYS[days],
      end_date: "2026-11-21",
    },
    summary: usageSummary,
    tasks,
    total_tasks: totalTasks,
    has_more: hasMore,
  };
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockCommonAPI(page: Page) {
  await page.route("**/api/v1/auth/csrf-token", (route) =>
    fulfillJson(route, { token: "personal-usage-csrf" }),
  );
  await page.route("**/api/v1/auth/me", (route) =>
    fulfillJson(route, REGULAR_USER),
  );
}

async function mockUsageAPI(
  page: Page,
  handler: (
    days: number,
    capability: string,
    offset: number,
    limit: number,
  ) => PersonalUsageResponse,
) {
  await page.route("**/api/usage?**", (route) => {
    const url = new URL(route.request().url());
    const body = handler(
      Number(url.searchParams.get("days")),
      url.searchParams.get("capability") ?? "all",
      Number(url.searchParams.get("offset")),
      Number(url.searchParams.get("limit")),
    );
    return fulfillJson(route, body);
  });
}

async function openPersonalUsage(
  page: Page,
  options: { sidebar?: "open" } = {},
) {
  await page.context().addCookies([
    { name: "access_token", value: "personal-usage-e2e-session", url: APP_URL },
    { name: "locale", value: "zh-CN", url: APP_URL },
    {
      name: "sidebar_state",
      value: options.sidebar === "open" ? "true" : "false",
      url: APP_URL,
    },
  ]);
  const response = await page.goto("/workspace/console");
  expect(response?.status()).toBe(200);
  await expect(page.getByTestId("personal-usage-page")).toBeVisible();
  return response;
}

// ---- Layout helpers (§1.2 of the redesign plan) ----
//
// documentElement.scrollWidth is always <= clientWidth inside the workspace
// (the shell is h-screen overflow-hidden and the page root clips x), so
// horizontal containment is asserted on the task rows and numeric cells
// themselves, never on the document.
//
// Breakpoints: the component pairs `min-[820px]:` with `max-[820px]:`. Tailwind 4
// compiles those to `(width >= 820px)` and `(width < 820px)` respectively
// (`@media not all and (min-width:820px)`), so exactly 820px is the table and
// 819px the two-row card — the assertions below probe both sides of that edge.

type Box = { x: number; y: number; width: number; height: number };

// Geometry is read right after a viewport change. The layout itself is pure
// CSS, but a slow machine may still be mid-frame (font swap, a resize-driven
// effect), so every read is retried briefly; a real layout fault persists
// and still fails.
const LAYOUT_TIMEOUT_MS = 5_000;

function centerY(box: Box) {
  return box.y + box.height / 2;
}

function centerX(box: Box) {
  return box.x + box.width / 2;
}

async function boxOf(page: Page, testId: string): Promise<Box> {
  const box = await page.getByTestId(testId).boundingBox();
  if (!box) throw new Error(`bounding box of ${testId} not found`);
  return box;
}

async function containmentOffenders(page: Page) {
  return page.evaluate(() =>
    Array.from(
      document.querySelectorAll<HTMLElement>(
        "[data-testid^='task-row-'], [data-testid^='task-cost-'], [data-testid^='task-tokens-']",
      ),
    )
      .filter((node) => node.scrollWidth > node.clientWidth)
      .map(
        (node) =>
          `${node.dataset.testid}: ${node.scrollWidth} > ${node.clientWidth}`,
      ),
  );
}

async function expectContained(page: Page) {
  await expect
    .poll(() => containmentOffenders(page), { timeout: LAYOUT_TIMEOUT_MS })
    .toEqual([]);
}

async function expectContentCentered(page: Page) {
  await expect(async () => {
    const content = await boxOf(page, "usage-layout-content");
    const pageRoot = await boxOf(page, "personal-usage-page");
    expect(content.width).toBeLessThanOrEqual(1120.5);
    expect(Math.abs(centerX(content) - centerX(pageRoot))).toBeLessThanOrEqual(
      1,
    );
  }).toPass({ timeout: LAYOUT_TIMEOUT_MS });
}

async function expectToolbarOnOneRow(page: Page) {
  const toolbar = page.getByTestId("usage-filter-toolbar");
  await expect(toolbar).toBeVisible();
  await expect(toolbar.getByTestId("usage-filter-scroll")).toHaveCount(1);
  // Six capabilities plus "全部": a product constant, mirrored by the unit test.
  await expect(toolbar.getByRole("button")).toHaveCount(7);
  await expect(async () => {
    const layout = await toolbar.evaluate((node) => {
      const box = (element: Element): Box => {
        const { x, y, width, height } = element.getBoundingClientRect();
        return { x, y, width, height };
      };
      const label = node.querySelector(":scope > span");
      if (!label) throw new Error("filter toolbar label not found");
      return {
        label: box(label),
        buttons: Array.from(node.querySelectorAll("button"), box),
      };
    });
    const firstTop = Math.min(...layout.buttons.map((button) => button.y));
    for (const button of layout.buttons) {
      expect(Math.abs(button.y - firstTop)).toBeLessThanOrEqual(1);
      expect(
        Math.abs(centerY(button) - centerY(layout.label)),
      ).toBeLessThanOrEqual(4);
    }
  }).toPass({ timeout: LAYOUT_TIMEOUT_MS });
}

async function expectSummarySideBySide(page: Page) {
  await expect(async () => {
    const cost = await boxOf(page, "usage-summary-cost");
    const tokens = await boxOf(page, "usage-summary-tokens");
    expect(Math.abs(centerY(cost) - centerY(tokens))).toBeLessThanOrEqual(1);
    expect(cost.x + cost.width).toBeLessThanOrEqual(tokens.x + 0.5);
  }).toPass({ timeout: LAYOUT_TIMEOUT_MS });
}

async function expectSummaryStacked(page: Page) {
  await expect(async () => {
    const cost = await boxOf(page, "usage-summary-cost");
    const tokens = await boxOf(page, "usage-summary-tokens");
    expect(cost.y + cost.height).toBeLessThanOrEqual(tokens.y + 0.5);
    expect(Math.abs(cost.x - tokens.x)).toBeLessThanOrEqual(1);
    expect(Math.abs(cost.width - tokens.width)).toBeLessThanOrEqual(1);
  }).toPass({ timeout: LAYOUT_TIMEOUT_MS });
}

async function rowGeometry(page: Page) {
  return page.evaluate(() => {
    // Name the missing node instead of failing on a null dereference.
    const required = <T>(node: T | null | undefined, what: string): T => {
      if (!node) throw new Error(`${what} not found`);
      return node;
    };
    const row = required(
      document.querySelector<HTMLElement>("[data-testid^='task-row-']"),
      "first task row",
    );
    const rowTestId = required(row.dataset.testid, "task row testid");
    const id = rowTestId.slice("task-row-".length);
    const box = (element: Element): Box => {
      const { x, y, width, height } = element.getBoundingClientRect();
      return { x, y, width, height };
    };
    const cell = (testId: string) =>
      required(
        row.querySelector<HTMLElement>(`[data-testid='${testId}']`),
        testId,
      );
    const title = cell(`task-title-${id}`);
    const tokens = cell(`task-tokens-${id}`);
    const cost = cell(`task-cost-${id}`);
    const chevron = cell(`task-chevron-${id}`);
    return {
      rowHeight: row.getBoundingClientRect().height,
      titleWhiteSpace: getComputedStyle(title).whiteSpace,
      numericAlign: [tokens, cost].map(
        (node) => getComputedStyle(node).textAlign,
      ),
      title: box(title),
      cost: box(cost),
      tokens: box(tokens),
      status: box(
        required(
          row.querySelector("[data-testid^='task-status-']"),
          `status of ${rowTestId}`,
        ),
      ),
      chevron: box(chevron),
    };
  });
}

/** ≥820px: column header visible, all five cells share one ≥70px row. */
async function expectTableLayout(page: Page) {
  await expect(page.getByTestId("usage-task-columns")).toBeVisible();
  await expect(async () => {
    const geometry = await rowGeometry(page);
    expect(geometry.rowHeight).toBeGreaterThanOrEqual(70);
    expect(geometry.titleWhiteSpace).toBe("nowrap");
    expect(geometry.numericAlign).toEqual(["right", "right"]);
    const cells = [
      geometry.title,
      geometry.status,
      geometry.tokens,
      geometry.cost,
      geometry.chevron,
    ];
    for (const cell of cells) {
      expect(
        Math.abs(centerY(cell) - centerY(geometry.title)),
      ).toBeLessThanOrEqual(4);
    }
    const leftEdges = cells.map((node) => node.x);
    expect(leftEdges).toEqual([...leftEdges].sort((a, b) => a - b));
  }).toPass({ timeout: LAYOUT_TIMEOUT_MS });
  await expectContained(page);
}

/** <820px: column header hidden, title/cost on row one, status/tokens on row two. */
async function expectCardLayout(page: Page) {
  await expect(page.getByTestId("usage-task-columns")).toBeHidden();
  await expect(async () => {
    const geometry = await rowGeometry(page);
    expect(geometry.titleWhiteSpace).toBe("normal");
    expect(geometry.numericAlign).toEqual(["right", "right"]);
    expect(
      Math.abs(centerY(geometry.title) - centerY(geometry.cost)),
    ).toBeLessThanOrEqual(4);
    expect(
      Math.abs(centerY(geometry.status) - centerY(geometry.tokens)),
    ).toBeLessThanOrEqual(4);
    expect(centerY(geometry.status)).toBeGreaterThan(
      centerY(geometry.title) + 10,
    );
    expect(geometry.cost.x).toBeGreaterThanOrEqual(
      geometry.title.x + geometry.title.width,
    );
    expect(centerY(geometry.chevron)).toBeGreaterThan(
      centerY(geometry.title) + 2,
    );
    expect(centerY(geometry.chevron)).toBeLessThan(
      centerY(geometry.status) - 2,
    );
  }).toPass({ timeout: LAYOUT_TIMEOUT_MS });
  await expectContained(page);
}

/** The element is a real horizontal scroller: content wider than its box, and it moves. */
async function expectScrollsInOwnContainer(scroller: Locator) {
  await expect(async () => {
    const dimensions = await scroller.evaluate((element) => ({
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
    }));
    expect(dimensions.scrollWidth).toBeGreaterThan(dimensions.clientWidth);
  }).toPass({ timeout: LAYOUT_TIMEOUT_MS });
  await scroller.evaluate((element) => {
    element.scrollLeft = 80;
  });
  expect(
    await scroller.evaluate((element) => element.scrollLeft),
  ).toBeGreaterThan(0);
}

async function expectFilterScrollsInItsOwnContainer(page: Page) {
  const scroller = page.getByTestId("usage-filter-scroll");
  await expect(async () => {
    const toolbarOverflows = await scroller.evaluate(
      (element) =>
        element.parentElement!.scrollWidth > element.parentElement!.clientWidth,
    );
    expect(toolbarOverflows).toBe(false);
  }).toPass({ timeout: LAYOUT_TIMEOUT_MS });
  await expectScrollsInOwnContainer(scroller);
}

async function expectAllTaskFieldsInsideViewport(page: Page) {
  await expect(async () => {
    const outside = await page.evaluate(() => {
      const width = window.innerWidth;
      const fields = document.querySelectorAll<HTMLElement>(
        "[data-testid^='task-title-'], [data-testid^='task-status-'], [data-testid^='task-tokens-'], [data-testid^='task-cost-']",
      );
      return Array.from(fields)
        .filter((field) => {
          const rect = field.getBoundingClientRect();
          return (
            rect.width === 0 || rect.left < -0.5 || rect.right > width + 0.5
          );
        })
        .map((field) => field.dataset.testid ?? field.tagName);
    });
    expect(outside).toEqual([]);
  }).toPass({ timeout: LAYOUT_TIMEOUT_MS });
}

// task-05 carries these so numeric cells are proven to wrap rather than clip
// or squeeze the title column; every other priced task costs UNIT_COST.
const HUGE_COST = 1234567.89;
const HUGE_TOKENS = 9876543210987;
const UNIT_COST = 1;
const UNIT_TOKENS = 10;
const WINDOW_DAYS: UsageDays = 7;

function pagedFixture() {
  const longModel = `vendor/${"very-long-model-segment-".repeat(6)}end`;
  const tasks = Array.from({ length: 21 }, (_, index) => {
    const id = `task-${String(index).padStart(2, "0")}`;
    const empty = index === 20;
    let title = `任务 ${index}`;
    if (index === 3) title = "历史媒体任务";
    if (index === 4) title = "未计价媒体任务";
    let status: UsageTask["status"] = "completed";
    if (index === 1 || empty) status = "running";
    else if (index === 2) status = "incomplete";
    let cost: number | null = index === 5 ? HUGE_COST : UNIT_COST;
    let totalTokens: number | null = index === 5 ? HUGE_TOKENS : UNIT_TOKENS;
    if (empty) {
      cost = null;
      totalTokens = null;
    }
    return task(id, index === 0 ? "course-video" : "ai_image", {
      title,
      status,
      cost,
      llm_cost: cost,
      media_cost: null,
      currency: empty ? null : "CNY",
      cost_source: empty ? null : "real",
      total_tokens: totalTokens,
      models: index === 0 ? [model(longModel, 8, 2, 0, 1, "real")] : [],
    });
  });
  // The summary is a fixed mock, so it is derived from the same values to
  // keep the page reconciled: ¥1,234,586.89 and 9,876,543,211,177.
  const totalCost = tasks.reduce((sum, item) => sum + (item.cost ?? 0), 0);
  const totalTokens = tasks.reduce(
    (sum, item) => sum + (item.total_tokens ?? 0),
    0,
  );
  expect(totalCost).toBeCloseTo(1234586.89, 2);
  expect(totalTokens).toBe(9876543211177);
  const fullSummary = summary(totalCost, totalTokens, "real");
  return { longModel, tasks, fullSummary };
}

function mockPagedUsage(
  page: Page,
  tasks: UsageTask[],
  fullSummary: PersonalUsageResponse["summary"],
) {
  return mockUsageAPI(page, (_days, _capability, offset, limit) => {
    expect(limit).toBe(20);
    return response(
      WINDOW_DAYS,
      tasks.slice(offset, offset + limit),
      fullSummary,
      tasks.length,
      offset + limit < tasks.length,
    );
  });
}

let legacyRequests: string[];

test.beforeEach(async ({ page }) => {
  legacyRequests = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (
      path.startsWith("/api/console/") ||
      path === "/api/admin/model-pricing" ||
      path === "/api/admin/media-pricing"
    ) {
      legacyRequests.push(request.url());
    }
  });
  await mockCommonAPI(page);
});

test.afterEach(() => {
  expect(legacyRequests).toEqual([]);
});

test("SSR regular identity, windows, filters, two-model reconciliation, and source labels", async ({
  page,
}) => {
  const thread = task("thread-a", "course-video", {
    title: "真实费用课程",
    cost: 1.5,
    llm_cost: 1.5,
    total_tokens: 100,
    models: [model("real-model", 60, 40, null, 1.5, "real")],
  });
  const poster = task("poster-b", "creative-studio", {
    title: "海报任务",
    status: "incomplete",
    cost: 0.5036,
    llm_cost: 0.0036,
    media_cost: 0.5,
    cost_source: "mixed",
    total_tokens: 200,
    models: [
      model("priced-model", 100, 50, 0, 0.002, "estimated"),
      model("real-model-2", 20, 30, null, 0.0016, "real"),
    ],
  });
  const video14 = task("video-14", "ai_video", {
    title: "十四天视频",
    status: "running",
    cost: 2,
    llm_cost: 2,
    cost_source: "real",
    total_tokens: 400,
    models: [model("model-14", 250, 150, 0, 2, "real")],
  });
  const image30 = task("image-30", "ai_image", {
    title: "三十天图片",
    cost: 3,
    llm_cost: 3,
    total_tokens: 600,
    models: [model("model-30", 400, 200, null, 3, "real")],
  });

  await mockUsageAPI(page, (days, capability) => {
    if (capability === "creative-studio")
      return response(7, [poster], summary(0.5036, 200, "mixed"));
    if (capability === "course-video")
      return response(7, [thread], summary(1.5, 100, "real"));
    if (days === 14)
      return response(
        14,
        [thread, poster, video14],
        summary(4.0036, 700, "mixed"),
      );
    if (days === 30)
      return response(
        30,
        [thread, poster, video14, image30],
        summary(7.0036, 1300, "mixed"),
      );
    return response(7, [thread, poster], summary(2.0036, 300, "mixed"));
  });

  const ssrResponse = await openPersonalUsage(page);
  const html = await ssrResponse!.text();
  expect(html).toContain('data-testid="personal-usage-page"');
  expect(html).not.toContain("运营后台");
  await expect(page.getByTestId("usage-days-7")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByTestId("usage-summary-cost")).toContainText("¥2.00");
  await expect(page.getByTestId("usage-summary-tokens")).toContainText("300");

  // Desktop and medium widths: capped centered content, header + window
  // control together, one toolbar row, side-by-side totals, a real table.
  const originalViewport = page.viewportSize()!;
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 1024, height: 768 },
  ]) {
    await page.setViewportSize(viewport);
    await expectContentCentered(page);
    await expect(
      page.getByTestId("usage-header").getByTestId("usage-window-control"),
    ).toHaveCount(1);
    await expectToolbarOnOneRow(page);
    await expectSummarySideBySide(page);
    await expectTableLayout(page);
  }
  await page.setViewportSize(originalViewport);

  await page.getByTestId("task-row-poster-b").click();
  await expect(
    page.getByTestId("usage-task-panel").getByTestId("task-details-poster-b"),
  ).toBeVisible();
  await expect(page.getByTestId("model-name-0")).toHaveText("priced-model");
  await expect(page.getByTestId("model-name-1")).toHaveText("real-model-2");
  await expect(page.getByTestId("model-input-0")).toHaveText("100");
  await expect(page.getByTestId("model-output-0")).toHaveText("50");
  await expect(page.getByTestId("model-cache-0")).toHaveText("0");
  await expect(page.getByTestId("model-total-0")).toHaveText("150");
  await expect(page.getByTestId("model-cache-1")).toHaveText("—");
  await expect(page.getByTestId("model-total-1")).toHaveText("50");
  await expect(page.getByTestId("task-tokens-poster-b")).toContainText("200");
  await expect(page.getByTestId("cost-equation-poster-b")).toContainText(
    "¥0.0036 + ¥0.5000 = ¥0.5036",
  );
  await expect(page.getByTestId("task-status-incomplete")).toHaveText("未完成");
  await expect(page.getByTestId("task-cost-poster-b")).toContainText("¥0.5036");

  await page.getByTestId("task-row-thread-a").click();
  await expect(page.getByTestId("task-cost-source-thread-a")).toHaveText(
    "供应商回传",
  );
  await expect(page.getByTestId("task-cost-source-thread-a")).not.toContainText(
    "估算",
  );
  await expect(page.getByTestId("open-original-thread-a")).toHaveAttribute(
    "href",
    "/workspace/agents/course-video/chats/thread-a",
  );

  await page.getByTestId("usage-days-14").click();
  await expect(page.getByTestId("usage-summary-cost")).toContainText("¥4.00");
  await expect(page.getByTestId("usage-summary-tokens")).toContainText("700");
  await expect(page.getByTestId("task-row-video-14")).toBeVisible();
  await page.getByTestId("task-row-video-14").click();
  await expect(page.getByTestId("model-name-0")).toHaveText("model-14");
  await expect(page.getByTestId("open-original-video-14")).toHaveAttribute(
    "href",
    "/workspace/video-gen/video-14",
  );

  await page.getByTestId("usage-days-30").click();
  await expect(page.getByTestId("usage-summary-cost")).toContainText("¥7.00");
  await expect(page.getByTestId("usage-summary-tokens")).toContainText("1,300");
  await page.getByTestId("task-row-image-30").click();
  await expect(page.getByTestId("model-name-0")).toHaveText("model-30");

  await page.getByTestId("usage-days-7").click();
  await page.getByTestId("usage-capability-creative-studio").click();
  await expect(page.getByTestId("usage-summary-cost")).toContainText("¥0.5036");
  await expect(page.getByTestId("usage-summary-tokens")).toContainText("200");
  await expect(page.getByTestId("task-row-poster-b")).toBeVisible();
  await expect(page.getByTestId("task-row-thread-a")).toHaveCount(0);
  await expect(page.getByTestId("usage-page-info")).toContainText("1–1 / 1");

  await page.getByTestId("usage-capability-course-video").click();
  await expect(page.getByTestId("usage-summary-cost")).toContainText("¥1.50");
  await expect(page.getByTestId("usage-summary-tokens")).toContainText("100");
  await expect(page.getByTestId("task-row-thread-a")).toBeVisible();
  await expect(page.getByTestId("task-row-poster-b")).toHaveCount(0);
});

test("pagination, statuses, keyboard, unknown media, and narrow-screen containment", async ({
  page,
}) => {
  const { longModel, tasks, fullSummary } = pagedFixture();
  await mockPagedUsage(page, tasks, fullSummary);
  await openPersonalUsage(page);

  await expect(
    page.getByRole("list", { name: "用量任务" }).getByRole("listitem"),
  ).toHaveCount(20);
  await expect(page.getByTestId("usage-summary-cost")).toContainText(
    "¥1,234,586.89",
  );
  await expect(page.getByTestId("usage-summary-tokens")).toContainText(
    "9,876,543,211,177",
  );
  await expect(page.getByTestId("usage-page-info")).toContainText("1–20 / 21");
  await expect(page.getByTestId("task-running-note-task-01")).toHaveText(
    "截至目前",
  );
  await expect(page.getByTestId("task-status-incomplete")).toHaveText("未完成");
  await expect(page.getByTestId("task-cost-task-02")).toContainText("¥1.00");

  // Huge values wrap inside their own cells; the totals above reconcile with them.
  await expect(page.getByTestId("task-tokens-task-05")).toContainText(
    "9,876,543,210,987",
  );
  await expect(page.getByTestId("task-cost-task-05")).toContainText(
    "¥1,234,567.89",
  );
  await expectContained(page);

  // 820px keeps the five-column table; 819px switches to the two-row card.
  const originalViewport = page.viewportSize()!;
  await page.setViewportSize({ width: 820, height: 900 });
  await expectTableLayout(page);
  await expectSummarySideBySide(page);
  await page.setViewportSize({ width: 819, height: 900 });
  await expectCardLayout(page);
  await expectSummarySideBySide(page);
  await page.setViewportSize(originalViewport);

  for (const id of ["task-03", "task-04"]) {
    await page.getByTestId(`task-row-${id}`).click();
    await expect(page.getByTestId(`task-media-cost-${id}`)).toHaveText(
      "媒体费用未核算",
    );
    await expect(page.getByTestId(`task-media-cost-${id}`)).not.toContainText(
      "未归因",
    );
    await expect(page.getByTestId(`task-media-cost-${id}`)).not.toContainText(
      "¥0.00",
    );
    await expect(page.getByTestId(`task-details-${id}`)).toContainText(
      "暂无模型明细",
    );
  }

  const firstRow = page.getByTestId("task-row-task-00");
  await firstRow.focus();
  await expect(firstRow).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("task-row-task-01")).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(firstRow).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("task-details-task-00")).toBeVisible();
  await expect(page.getByTestId("model-name-0")).toHaveText(longModel);
  await page.keyboard.press("Space");
  await expect(page.getByTestId("task-details-task-00")).toHaveCount(0);
  await page.keyboard.press("Enter");

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            document.documentElement.scrollWidth <=
            document.documentElement.clientWidth,
        ),
      )
      .toBe(true);
    await expectCardLayout(page);
    await expectSummaryStacked(page);
    await expectFilterScrollsInItsOwnContainer(page);
    await expectAllTaskFieldsInsideViewport(page);
    for (const testId of [
      "task-title-task-05",
      "task-status-completed",
      "task-tokens-task-05",
      "task-cost-task-05",
    ]) {
      await expect(page.getByTestId(testId).first()).toBeVisible();
    }
    await expectScrollsInOwnContainer(page.getByTestId("model-table-scroll"));
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
  }

  await page.getByTestId("usage-next-page").click();
  await expect(
    page.getByRole("list", { name: "用量任务" }).getByRole("listitem"),
  ).toHaveCount(1);
  await expect(page.getByTestId("usage-page-info")).toContainText("21–21 / 21");
  await expect(page.getByTestId("usage-summary-cost")).toContainText(
    "¥1,234,586.89",
  );
  await expect(page.getByTestId("usage-summary-tokens")).toContainText(
    "9,876,543,211,177",
  );
  await expect(page.getByTestId("task-row-task-20")).toContainText("核算中");
  await page.getByTestId("usage-previous-page").click();
  await expect(
    page.getByRole("list", { name: "用量任务" }).getByRole("listitem"),
  ).toHaveCount(20);

  await firstRow.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("task-details-task-00")).toBeVisible();
  const original = page.getByTestId("open-original-task-00");
  await original.focus();
  await expect(original).toBeFocused();
  await page.evaluate(() => {
    document.addEventListener(
      "click",
      (event) => {
        const target = event.target as HTMLElement | null;
        if (target?.closest("[data-testid='open-original-task-00']"))
          event.preventDefault();
      },
      { once: true },
    );
  });
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/workspace\/console$/);
  await expect(page.getByTestId("task-details-task-00")).toBeVisible();
  await Promise.all([
    page.waitForURL("**/workspace/agents/course-video/chats/task-00"),
    page.keyboard.press("Enter"),
  ]);
});

test("keeps numeric columns contained with the sidebar expanded", async ({
  page,
}) => {
  const { tasks, fullSummary } = pagedFixture();
  await mockPagedUsage(page, tasks, fullSummary);
  await openPersonalUsage(page, { sidebar: "open" });

  // The expanded desktop sidebar (256px instead of 48px) leaves these two
  // viewports with the least content width: 1050px is the desktop grid's
  // floor and 820px the medium grid's.
  for (const viewport of [
    { width: 1050, height: 900 },
    { width: 820, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    // Precondition: the expanded sidebar really pushes the page 256px in.
    await expect(async () => {
      const pageRoot = await boxOf(page, "personal-usage-page");
      expect(pageRoot.x).toBeGreaterThanOrEqual(250);
    }).toPass({ timeout: LAYOUT_TIMEOUT_MS });
    await expectTableLayout(page);
  }
});
