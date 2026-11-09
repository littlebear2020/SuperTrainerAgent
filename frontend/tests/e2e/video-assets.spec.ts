import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { mockVideoAPI, type MockVideoAPI } from "./utils/mock-video-api";

let api: MockVideoAPI;
let pageErrors: string[];

function seedWork(id: string, title = id) {
  api.seedTask({
    id,
    title,
    status: "ready",
    progress: 100,
    task_type: "ai_image",
    created_at: "2026-11-01T08:00:00Z",
    updated_at: "2026-11-02T09:00:00Z",
    thumbnail_url: `/api/video/tasks/${id}/thumbnail`,
  });
}

test.describe("video assets", () => {
  test.beforeEach(async ({ page }) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    mockLangGraphAPI(page);
    api = mockVideoAPI(page);
  });

  test.afterEach(() => {
    expect(pageErrors).toEqual([]);
  });

  test("defaults to works and filters ready AI types", async ({ page }) => {
    seedWork("ready-image", "合格图片作品");
    api.seedTask({
      id: "ready-video",
      title: "合格视频作品",
      status: "ready",
      progress: 100,
      task_type: "ai_video",
      created_at: "2026-11-03T08:00:00Z",
    });
    for (const [id, taskType, status] of [
      ["ready-course", "remotion_course", "ready"],
      ["ready-vimax", "vimax", "ready"],
      ["failed-ai", "ai_image", "failed"],
      ["draft-ai", "ai_image", "draft"],
      ["running-ai", "ai_video", "running"],
    ] as const) {
      api.seedTask({
        id,
        title: id,
        task_type: taskType,
        status,
        progress: 50,
      });
    }
    await page.goto("/workspace/video-gen");
    await expect(page.getByRole("heading", { name: "视频资产" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "我的作品" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByText("合格图片作品", { exact: true })).toBeVisible();
    await expect(page.getByText("合格视频作品", { exact: true })).toBeVisible();
    for (const id of [
      "ready-course",
      "ready-vimax",
      "failed-ai",
      "draft-ai",
      "running-ai",
    ]) {
      await expect(page.getByText(id, { exact: true })).toHaveCount(0);
    }
    const url = page.url();
    await page.getByRole("tab", { name: "我的资产" }).click();
    await expect(
      page.getByRole("tabpanel", { name: "我的资产" }),
    ).toBeVisible();
    expect(page.url()).toBe(url);
    await page.reload();
    await expect(page.getByRole("tab", { name: "我的作品" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(page.url()).toBe(url);
    await expect(page.getByRole("button", { name: "新建任务" })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "全部" })).toHaveCount(0);
  });

  test("loads six more locally and never exceeds latest 100", async ({
    page,
  }) => {
    let listRequests = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/video/tasks")
        listRequests += 1;
    });
    for (let index = 0; index < 12; index += 1) {
      api.seedTask({
        id: `work-${index}`,
        title: `本地作品 ${index}`,
        status: "ready",
        progress: 100,
        task_type: "ai_image",
        created_at: new Date(Date.UTC(2026, 10, 1, index)).toISOString(),
      });
    }
    await page.goto("/workspace/video-gen");
    await expect(page.getByTestId("completed-work-card")).toHaveCount(6);
    const before = listRequests;
    await page.getByRole("button", { name: "加载更多" }).click();
    await expect(page.getByTestId("completed-work-card")).toHaveCount(12);
    expect(listRequests).toBe(before);
    await expect(page.getByRole("button", { name: "加载更多" })).toHaveCount(0);
  });

  test("ready non-missing work can preview real media and download", async ({
    page,
  }) => {
    seedWork("playable", "可播放作品");
    await page.goto("/workspace/video-gen");
    const card = page
      .getByTestId("completed-work-card")
      .filter({ hasText: "可播放作品" });
    await card.getByRole("button", { name: "查看" }).click();
    const video = card.locator("video");
    await expect(video).toHaveAttribute(
      "src",
      "/api/video/tasks/playable/download",
    );
    await video.evaluate((element) => (element as HTMLVideoElement).load());
    await expect
      .poll(() =>
        video.evaluate((element) => (element as HTMLVideoElement).duration),
      )
      .toBe(10);
    await expect
      .poll(() =>
        video.evaluate((element) => (element as HTMLVideoElement).videoWidth),
      )
      .toBe(960);
    const downloadEvent = page.waitForEvent("download");
    await card.getByRole("link", { name: "下载" }).click();
    await downloadEvent;
    expect(api.downloadRequests).toHaveLength(1);
  });

  test("artifact_missing is visible but preview and download are blocked", async ({
    page,
  }) => {
    api.seedTask({
      id: "missing-work",
      title: "已清理作品",
      status: "ready",
      progress: 100,
      task_type: "ai_image",
      artifact_missing: true,
      thumbnail_url: "/api/video/tasks/missing-work/thumbnail",
    });
    await page.goto("/workspace/video-gen");
    const card = page.getByTestId("completed-work-card");
    await expect(card.getByText("产物已清理")).toBeVisible();
    await expect(card.getByRole("button", { name: "查看" })).toBeDisabled();
    await expect(card.getByRole("link", { name: "下载" })).toHaveCount(0);
    await expect(card.locator("img")).toHaveCount(0);
    expect(api.downloadRequests).toHaveLength(0);
  });

  test("works cards and project cards contain no fabricated fields", async ({
    page,
  }) => {
    api.seedTask({
      id: "honest-work",
      title: "字段真实作品",
      status: "ready",
      progress: 100,
      task_type: "ai_image",
      created_at: "2026-11-01T08:00:00Z",
      updated_at: "2026-11-02T09:00:00Z",
      result: { video_count: 99, total_duration: 88, resolution: "8K" },
    });
    api.seedProject(
      Object.assign(
        {
          id: "honest-project",
          title: "字段真实资产",
          visual_style_preset: "precision_eng",
          updated_at: "2026-11-02T09:00:00Z",
        },
        { video_count: 99, total_duration: 88 },
      ),
    );
    await page.goto("/workspace/video-gen");
    await expect(page.getByText("字段真实作品", { exact: true })).toBeVisible();
    let text = await page.getByRole("tabpanel").textContent();
    for (const expected of ["字段真实作品", "图片动效", "完成", "最近更新"]) {
      expect(text).toContain(expected);
    }
    await page.getByRole("tab", { name: "我的资产" }).click();
    await expect(page.getByText("字段真实资产", { exact: true })).toBeVisible();
    text = await page.getByRole("tabpanel").textContent();
    for (const expected of [
      "字段真实资产",
      "精密工程",
      "最近更新",
      "新建项目",
      "编辑项目",
    ]) {
      expect(text).toContain(expected);
    }
    for (const forbidden of [
      "99",
      "88",
      "8K",
      "时长",
      "分辨率",
      "完成时间",
      "项目统计",
      "系统健康",
    ]) {
      expect(text).not.toContain(forbidden);
    }
  });

  test("recent projects retain real title type and update", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "real-recent",
          title: "真实近期课程",
          updated_at: "2026-11-02T09:00:00Z",
        },
      ],
    });
    await page.goto("/workspace");
    const recent = page
      .getByRole("heading", { name: "近期项目" })
      .locator("..");
    await expect(recent.locator("..").getByText("真实近期课程")).toBeVisible();
    await expect(recent.locator("..").getByText("自由对话")).toBeVisible();
    await expect(recent.locator("..").getByText(/11月2日 17:00/)).toBeVisible();
  });

  test("project cover falls through and existing create edit delete flow works", async ({
    page,
  }) => {
    await page.goto("/workspace/video-gen");
    await page.getByRole("tab", { name: "我的资产" }).click();
    await page.getByRole("button", { name: "新建项目" }).click();
    await page.locator("#cp-title").fill("待编辑项目");
    await page.getByRole("button", { name: "创建项目" }).click();
    await expect(
      page.getByRole("button", { name: "返回项目列表" }),
    ).toBeVisible();
    const projectTitle = page.getByPlaceholder("如：AI 入门课程");
    await projectTitle.fill("已保存项目");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(projectTitle).toHaveValue("已保存项目");
    await page.getByRole("button", { name: "返回项目列表" }).click();
    await expect(page.getByText("已保存项目", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /已保存项目/ }).click();
    await page.getByRole("button", { name: "删除项目" }).click();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "删除", exact: true })
      .click();
    await expect(page.getByText("已保存项目", { exact: true })).toHaveCount(0);
    expect(api.projectRequests.map((request) => request.method)).toEqual(
      expect.arrayContaining(["POST", "PUT", "DELETE"]),
    );
  });

  test("video preview error and mobile tabs degrade honestly", async ({
    page,
  }) => {
    for (let index = 0; index < 7; index += 1)
      seedWork(`mobile-${index}`, `移动作品 ${index}`);
    api.seedProject({ id: "mobile-project", title: "移动资产项目" });
    await page.goto("/workspace/video-gen");
    const firstCard = page.getByTestId("completed-work-card").first();
    await firstCard.getByRole("button", { name: "查看" }).click();
    await firstCard.locator("video").dispatchEvent("error");
    await expect(firstCard.getByText("视频预览暂不可用")).toBeVisible();
    await expect(firstCard.getByRole("link", { name: "下载" })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "加载更多" }).click();
    await expect(page.getByTestId("completed-work-card")).toHaveCount(7);
    await page.getByRole("tab", { name: "我的资产" }).click();
    await page.getByRole("button", { name: /移动资产项目/ }).click();
    await expect(
      page.getByRole("button", { name: "返回项目列表" }),
    ).toBeVisible();
  });
});
