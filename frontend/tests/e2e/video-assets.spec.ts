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

test.use({ timezoneId: "Asia/Shanghai" });

test.describe("video assets", () => {
  test.beforeEach(async ({ page }) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    mockLangGraphAPI(page);
    api = mockVideoAPI(page);
    api.clearTasks();
  });

  test.afterEach(() => {
    expect(pageErrors).toEqual([]);
  });

  test("defaults to AI image list and shows all statuses", async ({ page }) => {
    seedWork("ready-image", "合格图片作品");
    api.seedTask({
      id: "visual-ai",
      title: "visual-ai",
      status: "draft",
      progress: 45,
      task_type: "ai_visual",
    });
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
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
    await expect(page.getByRole("heading", { name: "视频资产" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "我的作品" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByRole("tab", { name: "图片动效" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    for (const id of ["合格图片作品", "failed-ai", "draft-ai", "visual-ai"]) {
      await expect(page.getByText(id, { exact: true })).toBeVisible();
    }
    for (const id of [
      "合格视频作品",
      "running-ai",
      "ready-course",
      "ready-vimax",
    ]) {
      await expect(page.getByText(id, { exact: true })).toHaveCount(0);
    }
    await page.getByRole("tab", { name: "AI视频" }).click();
    await expect(page.getByText("合格视频作品", { exact: true })).toBeVisible();
    await expect(page.getByText("running-ai", { exact: true })).toBeVisible();
    for (const id of [
      "合格图片作品",
      "failed-ai",
      "ready-course",
      "ready-vimax",
    ]) {
      await expect(page.getByText(id, { exact: true })).toHaveCount(0);
    }
    await page.getByRole("tab", { name: "我的资产" }).click();
    await expect(
      page.getByRole("tabpanel", { name: "我的资产" }),
    ).toBeVisible();
    await expect(page).toHaveURL(/tab=assets/);
    await page.reload();
    await expect(page.getByRole("tab", { name: "我的资产" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page).toHaveURL(/tab=assets/);
    await expect(page.getByRole("button", { name: "新建任务" })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "全部" })).toHaveCount(0);
  });

  test("URL tabs and task details drive scoped navigation active state", async ({
    page,
  }) => {
    seedWork("ready-image", "合格图片作品");
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
    await expect(page.getByRole("link", { name: "我的作品" })).toHaveAttribute(
      "data-active",
      "true",
    );
    await expect(page.getByRole("link", { name: "我的资产" })).toHaveAttribute(
      "data-active",
      "false",
    );

    await page.getByRole("tab", { name: "我的资产" }).click();
    await expect(page).toHaveURL(/tab=assets/);
    await expect(page.getByRole("link", { name: "我的作品" })).toHaveAttribute(
      "data-active",
      "false",
    );
    await expect(page.getByRole("link", { name: "我的资产" })).toHaveAttribute(
      "data-active",
      "true",
    );

    await page.getByRole("tab", { name: "我的作品" }).click();
    await page.getByRole("tab", { name: "图片动效", exact: true }).click();
    await expect(page.getByText("合格图片作品", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "详情" }).click();
    await expect(page).toHaveURL(/\/workspace\/video-gen\/ready-image$/);
    await expect(page.getByRole("link", { name: "我的作品" })).toHaveAttribute(
      "data-active",
      "true",
    );
    await expect(page.getByRole("link", { name: "我的资产" })).toHaveAttribute(
      "data-active",
      "false",
    );
  });

  test("loads six more locally per type and resets preview and pagination", async ({
    page,
  }) => {
    let listRequests = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/video/tasks")
        listRequests += 1;
    });
    for (let index = 0; index < 7; index += 1) {
      api.seedTask({
        id: `image-${index}`,
        title: `本地图片 ${index}`,
        status: "ready",
        progress: 100,
        task_type: index === 6 ? "ai_visual" : "ai_image",
        created_at: new Date(Date.UTC(2026, 10, 1, index)).toISOString(),
      });
      api.seedTask({
        id: `video-${index}`,
        title: `本地视频 ${index}`,
        status: "ready",
        progress: 100,
        task_type: "ai_video",
        created_at: new Date(Date.UTC(2026, 10, 2, index)).toISOString(),
      });
    }
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
    await expect(page.getByTestId("completed-work-card")).toHaveCount(6);
    await page
      .getByTestId("completed-work-card")
      .first()
      .getByRole("button", { name: "查看" })
      .click();
    await expect(page.locator("video")).toHaveCount(1);
    const before = listRequests;
    await page.getByRole("button", { name: "加载更多" }).click();
    await expect(page.getByTestId("completed-work-card")).toHaveCount(7);
    await expect(page.getByText("本地图片 6", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "AI视频" }).click();
    await expect(page.getByTestId("completed-work-card")).toHaveCount(6);
    await expect(page.locator("video")).toHaveCount(0);
    await page.getByRole("button", { name: "加载更多" }).click();
    await expect(page.getByTestId("completed-work-card")).toHaveCount(7);
    await page.getByRole("tab", { name: "图片动效" }).click();
    await expect(page.getByTestId("completed-work-card")).toHaveCount(6);
    expect(listRequests).toBe(before);
    await expect(page.getByRole("button", { name: "加载更多" })).toBeVisible();
  });

  test("ready non-missing work can preview real media and download", async ({
    page,
  }) => {
    seedWork("playable", "可播放作品");
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
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
      .toBeGreaterThanOrEqual(8);
    await expect
      .poll(() =>
        video.evaluate((element) => (element as HTMLVideoElement).duration),
      )
      .toBeLessThanOrEqual(12);
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
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
    const card = page.getByTestId("completed-work-card");
    await expect(card.getByText("产物已清理")).toBeVisible();
    await expect(card.getByRole("button", { name: "查看" })).toBeDisabled();
    await expect(card.getByRole("link", { name: "下载" })).toHaveCount(0);
    await expect(card.locator("img")).toHaveCount(0);
    expect(api.downloadRequests).toHaveLength(0);
  });

  test("restores exact-id details storyboard retry and delete operations", async ({
    page,
  }) => {
    api.seedTask({
      id: "draft-edit",
      title: "草稿分镜作品",
      status: "draft",
      progress: 100,
      task_type: "ai_image",
    });
    api.seedTask({
      id: "failed-visual",
      title: "失败视觉作品",
      status: "failed",
      progress: 70,
      task_type: "ai_visual",
      error_message: "真实失败原因",
    });
    api.seedTask({
      id: "qc-only",
      title: "质检修复作品",
      status: "qc_repair",
      progress: 80,
      task_type: "ai_image",
    });
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
    const draft = page
      .getByTestId("completed-work-card")
      .filter({ hasText: "草稿分镜作品" });
    await draft.getByRole("button", { name: "详情" }).click();
    await expect(page).toHaveURL(/\/workspace\/video-gen\/draft-edit$/);
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
    await draft.getByRole("button", { name: "继续编辑分镜" }).click();
    await expect(page).toHaveURL(
      /\/workspace\/video-gen\/draft-edit\/storyboard$/,
    );

    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
    const failed = page
      .getByTestId("completed-work-card")
      .filter({ hasText: "失败视觉作品" });
    await expect(failed.getByText("真实失败原因")).toBeVisible();
    await failed.getByRole("button", { name: "重试合成" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "取消" })
      .click();
    expect(api.retryRequests).toHaveLength(0);
    api.failNextRetry(409);
    await failed.getByRole("button", { name: "重试合成" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "确认合成" })
      .click();
    await page.getByRole("button", { name: "去编辑分镜" }).click();
    await expect(page).toHaveURL(
      /\/workspace\/video-gen\/failed-visual\/storyboard$/,
    );
    expect(api.retryRequests.map((request) => request.url)).toEqual([
      "/api/video/tasks/failed-visual/recompose",
    ]);

    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
    const qc = page
      .getByTestId("completed-work-card")
      .filter({ hasText: "质检修复作品" });
    await expect(qc.getByRole("button", { name: "查看" })).toBeDisabled();
    await expect(qc.getByRole("link", { name: "下载" })).toHaveCount(0);
    await expect(qc.getByRole("button", { name: "重试合成" })).toHaveCount(0);
    await qc.getByRole("button", { name: "删除" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "取消" })
      .click();
    expect(api.deleteRequests).toHaveLength(0);
    await qc.getByRole("button", { name: "删除" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "删除", exact: true })
      .click();
    await expect(qc).toHaveCount(0);
    expect(api.deleteRequests.map((request) => request.url)).toEqual([
      "/api/video/tasks/qc-only",
    ]);
  });

  test("list failure can be retried manually without hiding the error", async ({
    page,
  }) => {
    seedWork("after-retry", "手动重试作品");
    api.failNextList(500);
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
    await expect(page.getByText("作品加载失败，请稍后重试")).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText("手动重试作品", { exact: true })).toHaveCount(
      0,
    );
    api.clearListFailure();
    await page.getByRole("button", { name: "重试" }).click();
    await expect(page.getByText("手动重试作品", { exact: true })).toBeVisible();
    await expect(page.getByText("作品加载失败，请稍后重试")).toHaveCount(0);
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
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
    await expect(page.getByText("字段真实作品", { exact: true })).toBeVisible();
    let text = await page
      .getByRole("tabpanel", { name: "图片动效", exact: true })
      .textContent();
    for (const expected of ["字段真实作品", "AI 生图", "完成", "最近更新"]) {
      expect(text).toContain(expected);
    }
    await page.getByRole("tab", { name: "我的资产" }).click();
    await expect(page.getByText("字段真实资产", { exact: true })).toBeVisible();
    text = await page
      .getByRole("tabpanel", { name: "我的资产", exact: true })
      .textContent();
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

  test("project cover falls through and existing create edit delete flow works", async ({
    page,
  }) => {
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
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
    await page.goto("/workspace/video-gen?tab=works&capability=ai_image");
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

  test("new project detail stays simplified and create posts only title and optional style", async ({
    page,
  }) => {
    await page.goto("/workspace/video-gen?tab=assets");
    await page.getByRole("button", { name: "新建项目" }).click();
    await page.locator("#cp-title").fill("新建精简项目");
    await page.getByRole("button", { name: "创建项目" }).click();
    await expect(
      page.getByRole("button", { name: "返回项目列表" }),
    ).toBeVisible();

    const create = api.projectRequests.find(
      (request) => request.method === "POST",
    );
    expect(create?.body).toEqual({ title: "新建精简项目" });
    const text = await page
      .getByRole("tabpanel", { name: "我的资产", exact: true })
      .textContent();
    for (const expected of [
      "基本设置",
      "角色参考图",
      "风格锚图",
      "道具资产",
      "保存",
    ]) {
      expect(text).toContain(expected);
    }
    for (const removed of ["品牌包", "色彩微调", "关联视频"]) {
      expect(text).not.toContain(removed);
    }
  });

  test("existing project save preserves brand palette extensions and related count", async ({
    page,
  }) => {
    const palette = {
      primary: "#112233",
      saturation: 1.25,
      contrast: 0.85,
      brightness: 0.15,
      grade: "legacy-grade",
      old_extension: 42,
    };
    const brandRequests: string[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (
        path.includes("/brand/logo-image") ||
        path.startsWith("/remotion-layouts/")
      ) {
        brandRequests.push(path);
      }
    });
    api.seedProject({
      id: "stored-project",
      title: "存量品牌项目",
      visual_style_preset: "flat",
      color_palette: palette,
      style_anchor_image: "anchor.png",
      remotion_theme: "warm",
      font_family: "Georgia,serif",
      logo_path: "brand/existing-logo.png",
      intro_outro: {
        intro: { headline: "保留片头", subtitle: "保留副标题" },
        outro: { headline: "保留片尾", follow: "保留关注语" },
      },
    });
    api.seedTask({
      id: "linked-course",
      title: "关联课程",
      status: "draft",
      progress: 100,
      task_type: "remotion_course",
      course_project_id: "stored-project",
    });
    api.upsertStoryboard("linked-course", {
      title: "课程分镜",
      config: { video_type: "remotion_course" },
      status: "draft",
      frames: [{ index: 0, narration: "固定旁白", duration: 4 }],
      audio_ready: true,
    });

    await page.goto("/workspace/video-gen?tab=assets");
    await page.getByRole("button", { name: /存量品牌项目/ }).click();
    await expect(
      page.getByRole("button", { name: "返回项目列表" }),
    ).toBeVisible();
    const text = await page
      .getByRole("tabpanel", { name: "我的资产", exact: true })
      .textContent();
    for (const removed of ["品牌包", "色彩微调", "关联视频"]) {
      expect(text).not.toContain(removed);
    }

    const anchorSection = page
      .getByRole("heading", { name: "风格锚图", exact: true })
      .locator("..");
    await anchorSection
      .getByRole("button", { name: "替换", exact: true })
      .click();
    await anchorSection.locator("input[type='file']").setInputFiles({
      name: "replacement.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000",
        "hex",
      ),
    });
    await anchorSection
      .getByRole("button", { name: "替换风格锚图", exact: true })
      .click();
    const replaceAnchorDialog = page.getByRole("alertdialog");
    await expect(replaceAnchorDialog).toContainText("本项目的 1 个关联任务");
    await replaceAnchorDialog.getByRole("button", { name: "取消" }).click();

    await page.getByPlaceholder("如：AI 入门课程").fill("仅修改项目标题");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect
      .poll(
        () =>
          api.projectRequests.filter((request) => request.method === "PUT")
            .length,
      )
      .toBe(1);
    const update = api.projectRequests.find(
      (request) => request.method === "PUT",
    );
    const body = update?.body as Record<string, unknown>;
    expect(body.title).toBe("仅修改项目标题");
    expect(body.color_palette).toEqual(palette);
    for (const brandField of [
      "remotion_theme",
      "font_family",
      "logo_path",
      "intro_outro",
    ]) {
      expect(body).not.toHaveProperty(brandField);
    }
    expect(JSON.stringify(body)).not.toContain("dark-blue");
    expect(api.project("stored-project")).toMatchObject({
      remotion_theme: "warm",
      font_family: "Georgia,serif",
      logo_path: "brand/existing-logo.png",
      color_palette: palette,
      intro_outro: {
        intro: { headline: "保留片头", subtitle: "保留副标题" },
        outro: { headline: "保留片尾", follow: "保留关注语" },
      },
    });
    expect(brandRequests).toEqual([]);

    await page.goto("/workspace/video-gen/linked-course/storyboard");
    await expect(
      page.getByRole("heading", { name: "关联课程", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("文字动效 · 1 帧")).toBeVisible();
  });
});
