import { expect, test } from "@playwright/test";

import { mockLangGraphAPI, type MockThread } from "./utils/mock-api";
import { mockVideoAPI, type MockVideoAPI } from "./utils/mock-video-api";

let api: MockVideoAPI;
let pageErrors: string[];

function seedVideo(
  id: string,
  title: string,
  taskType: "ai_image" | "ai_video",
) {
  api.seedTask({
    id,
    title,
    status: "ready",
    progress: 100,
    task_type: taskType,
    updated_at: "2026-11-02T09:00:00Z",
  });
}

test.describe("six work capabilities", () => {
  test.beforeEach(async ({ page }) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    api = mockVideoAPI(page);
    api.clearTasks();
  });

  test.afterEach(() => {
    expect(pageErrors).toEqual([]);
  });

  test("clicks all six types, restores URL history and opens canonical aliases", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "course-history",
          title: "真实文字动效对话",
          agent_name: "course-video",
        },
        {
          thread_id: "creative-history",
          title: "旧海报对话",
          project_type: "card-poster",
        },
        {
          thread_id: "ppt-history",
          title: "旧课件对话",
          project_type: "course-ppt",
        },
        {
          thread_id: "podcast-history",
          title: "真实播客对话",
          agent_name: "podcast-studio",
        },
      ],
    });
    seedVideo("image-work", "真实图片作品", "ai_image");
    seedVideo("video-work", "真实视频作品", "ai_video");

    await page.goto("/workspace/video-gen");
    await expect(
      page.locator("#work-capability-tabs [role='tab']"),
    ).toHaveCount(6);
    await expect(
      page
        .locator("#work-capability-panel-course-video")
        .getByText("真实文字动效对话", { exact: true }),
    ).toBeVisible();

    await page.getByRole("tab", { name: "图片动效", exact: true }).click();
    await expect(
      page
        .locator("#work-capability-panel-ai_image")
        .getByText("真实图片作品", { exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/capability=ai_image/);

    await page.getByRole("tab", { name: "AI视频", exact: true }).click();
    await expect(
      page
        .locator("#work-capability-panel-ai_video")
        .getByText("真实视频作品", { exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/capability=ai_video/);

    await page.getByRole("tab", { name: "课程海报", exact: true }).click();
    await expect(
      page
        .locator("#work-capability-panel-creative-studio")
        .getByText("旧海报对话", { exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/capability=creative-studio/);

    await page.getByRole("tab", { name: "PPT课件", exact: true }).click();
    await expect(
      page
        .locator("#work-capability-tabs + [role='tabpanel']")
        .getByText("旧课件对话", { exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/capability=ppt-master/);

    await page.getByRole("tab", { name: "语音播客", exact: true }).click();
    await expect(
      page
        .locator("#work-capability-tabs + [role='tabpanel']")
        .getByText("真实播客对话", { exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/capability=podcast-studio/);
    await page.reload();
    await expect(
      page
        .locator("#work-capability-tabs + [role='tabpanel']")
        .getByText("真实播客对话", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("tab", { name: "语音播客", exact: true }),
    ).toHaveAttribute("aria-selected", "true");

    await page.goBack();
    await expect(
      page
        .locator("#work-capability-tabs + [role='tabpanel']")
        .getByText("旧课件对话", { exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/capability=ppt-master/);
    await page.getByRole("button", { name: "继续对话", exact: true }).click();
    await expect(page).toHaveURL(
      /\/workspace\/agents\/ppt-master\/chats\/ppt-history$/,
    );

    await page.goto("/workspace/video-gen?tab=works&capability=podcast-studio");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      page
        .locator("#work-capability-tabs + [role='tabpanel']")
        .getByText("真实播客对话", { exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
  });

  test("fetches beyond offset 50, searches titles and opens the real thread", async ({
    page,
  }) => {
    const threads: MockThread[] = Array.from({ length: 50 }, (_, index) => ({
      thread_id: `course-${index}`,
      title: `普通课程 ${index}`,
      agent_name: "course-video",
      updated_at: new Date(Date.UTC(2026, 0, index + 1)).toISOString(),
    }));
    threads.push({
      thread_id: "offset-target",
      title: "Offset 五十目标",
      agent_name: "course-video",
      updated_at: "2024-01-01T00:00:00Z",
    });
    mockLangGraphAPI(page, { threads });
    const offsets: number[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/langgraph/threads/search") {
        const body = request.postDataJSON() as { offset?: number };
        offsets.push(body.offset ?? 0);
      }
    });

    await page.goto("/workspace/video-gen");
    await expect(page.getByTestId("agent-conversation-card")).toHaveCount(6);
    await page
      .getByRole("searchbox", { name: "搜索对话标题" })
      .fill("  oFFsEt 五十目标  ");
    await expect(page.getByTestId("agent-conversation-card")).toHaveCount(1);
    await expect(
      page
        .locator("#work-capability-tabs + [role='tabpanel']")
        .getByText("Offset 五十目标", { exact: true }),
    ).toBeVisible();
    expect(offsets).toContain(50);
    await page.getByRole("button", { name: "继续对话", exact: true }).click();
    await expect(page).toHaveURL(
      /\/workspace\/agents\/course-video\/chats\/offset-target$/,
    );
  });

  test("shows an honest podcast 404 and retries without fabricating an empty state", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "podcast-after-retry",
          title: "重试后的播客",
          agent_name: "podcast-studio",
        },
      ],
    });
    let fail = true;
    await page.route("**/api/langgraph/threads/search", (route) => {
      if (fail) {
        return route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: "podcast history unavailable" }),
        });
      }
      return route.fallback();
    });

    await page.goto("/workspace/video-gen?tab=works&capability=podcast-studio");
    await expect(
      page
        .locator("#work-capability-tabs + [role='tabpanel']")
        .getByText("对话加载失败，请稍后重试"),
    ).toBeVisible({
      timeout: 15000,
    });
    await expect(
      page
        .locator("#work-capability-tabs + [role='tabpanel']")
        .getByText("暂无语音播客对话"),
    ).toHaveCount(0);
    await expect(page.getByTestId("agent-conversation-card")).toHaveCount(0);

    fail = false;
    await page.getByRole("button", { name: "重试", exact: true }).click();
    await expect(
      page
        .locator("#work-capability-tabs + [role='tabpanel']")
        .getByText("重试后的播客", { exact: true }),
    ).toBeVisible();
    await expect(
      page
        .locator("#work-capability-tabs + [role='tabpanel']")
        .getByText("对话加载失败，请稍后重试"),
    ).toHaveCount(0);
  });
});
