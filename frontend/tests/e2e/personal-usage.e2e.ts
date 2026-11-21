import { expect, test, type Page, type Route } from "@playwright/test";

import type {
  PersonalUsageResponse,
  UsageCostSource,
  UsageDays,
  UsageModel,
  UsageTask,
} from "../../src/core/usage/types";

const APP_URL = `http://localhost:${process.env.E2E_PORT ?? 3127}`;
const REGULAR_USER = {
  id: "personal-usage-e2e-user",
  email: "personal-usage-e2e@test.local",
  system_role: "user",
  needs_setup: false,
};

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
  return {
    task_key: `${capability === "ai_image" || capability === "ai_video" ? "video" : "agent"}:${id}`,
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
      start_date:
        days === 7 ? "2026-11-15" : days === 14 ? "2026-11-08" : "2026-10-23",
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

async function openPersonalUsage(page: Page) {
  await page.context().addCookies([
    { name: "access_token", value: "personal-usage-e2e-session", url: APP_URL },
    { name: "locale", value: "zh-CN", url: APP_URL },
    { name: "sidebar_state", value: "false", url: APP_URL },
  ]);
  const response = await page.goto("/workspace/console");
  expect(response?.status()).toBe(200);
  await expect(page.getByTestId("personal-usage-page")).toBeVisible();
  return response;
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

  await page.getByTestId("task-row-poster-b").click();
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
  const longModel = `vendor/${"very-long-model-segment-".repeat(6)}end`;
  const tasks = Array.from({ length: 21 }, (_, index) => {
    const id = `task-${String(index).padStart(2, "0")}`;
    const empty = index === 20;
    return task(id, index === 0 ? "course-video" : "ai_image", {
      title:
        index === 3
          ? "历史媒体任务"
          : index === 4
            ? "未计价媒体任务"
            : `任务 ${index}`,
      status:
        index === 1 || empty
          ? "running"
          : index === 2
            ? "incomplete"
            : "completed",
      cost: empty ? null : 1,
      llm_cost: empty ? null : 1,
      media_cost: null,
      currency: empty ? null : "CNY",
      cost_source: empty ? null : "real",
      total_tokens: empty ? null : 10,
      models: index === 0 ? [model(longModel, 8, 2, 0, 1, "real")] : [],
    });
  });
  const fullSummary = summary(20, 200, "real");
  await mockUsageAPI(page, (_days, _capability, offset, limit) => {
    expect(limit).toBe(20);
    return response(
      7,
      tasks.slice(offset, offset + limit),
      fullSummary,
      21,
      offset + limit < 21,
    );
  });
  await openPersonalUsage(page);

  await expect(
    page.getByRole("list", { name: "用量任务" }).getByRole("listitem"),
  ).toHaveCount(20);
  await expect(page.getByTestId("usage-summary-cost")).toContainText("¥20.00");
  await expect(page.getByTestId("usage-summary-tokens")).toContainText("200");
  await expect(page.getByTestId("usage-page-info")).toContainText("1–20 / 21");
  await expect(page.getByTestId("task-running-note-task-01")).toHaveText(
    "截至目前",
  );
  await expect(page.getByTestId("task-status-incomplete")).toHaveText("未完成");
  await expect(page.getByTestId("task-cost-task-02")).toContainText("¥1.00");

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
    const scroller = page.getByTestId("model-table-scroll");
    const dimensions = await scroller.evaluate((element) => ({
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
    }));
    expect(dimensions.scrollWidth).toBeGreaterThan(dimensions.clientWidth);
    await scroller.evaluate((element) => {
      element.scrollLeft = 80;
    });
    expect(
      await scroller.evaluate((element) => element.scrollLeft),
    ).toBeGreaterThan(0);
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
  await expect(page.getByTestId("usage-summary-cost")).toContainText("¥20.00");
  await expect(page.getByTestId("usage-summary-tokens")).toContainText("200");
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
