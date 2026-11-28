import { readFileSync } from "node:fs";

import { expect, type Locator, type Page, test } from "@playwright/test";

import {
  mockLangGraphAPI,
  type MockArtifactFile,
  type MockArtifactSummary,
} from "./utils/mock-api";
import { mockVideoAPI, type MockVideoAPI } from "./utils/mock-video-api";

let videoAPI: MockVideoAPI;

// 真实 PNG（1920×1080），用于「同作品交付的光栅图作封面」用例；不用营销样例图冒充用户封面。
const PNG = readFileSync("public/remotion-layouts/full-visual-page.png");

function output(
  name: string,
  overrides: Partial<MockArtifactFile> = {},
): MockArtifactFile {
  return {
    path: `/mnt/user-data/outputs/${name}`,
    name,
    media_kind: name.endsWith(".mp4")
      ? "video"
      : name.endsWith(".png")
        ? "image"
        : "document",
    size: 1234,
    modified_at: "2026-11-02T09:00:00Z",
    exists: true,
    ...overrides,
  };
}

function summary(
  threadId: string,
  deliveryState: MockArtifactSummary["delivery_state"],
  files: MockArtifactFile[] = [],
): MockArtifactSummary {
  return { thread_id: threadId, delivery_state: deliveryState, files };
}

function taskCard(page: Page, id: string) {
  return page.locator(`[data-task-id="${id}"]`);
}

function typeCover(scope: Locator): Locator {
  return scope.getByTestId("work-type-cover");
}

function readyDelivery(fileName: string) {
  return {
    file_name: fileName,
    file_size: 100,
    file_updated_at: "2026-11-02T09:00:00Z",
    media_kind: "video" as const,
    exists: true,
  };
}

/** 只给该线程的 PNG 回真实图片，其余产物（HTML/SVG）一律 404 且记录。 */
async function serveOnlyPng(page: Page, threadId: string): Promise<string[]> {
  const requests: string[] = [];
  await page.route(`**/api/threads/${threadId}/artifacts/**`, (route) => {
    const path = new URL(route.request().url()).pathname;
    requests.push(path);
    if (path.toLocaleLowerCase().endsWith(".png")) {
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        body: PNG,
      });
    }
    return route.fulfill({ status: 404, body: "" });
  });
  return requests;
}

test.describe("works delivery center", () => {
  test.beforeEach(async ({ page }) => {
    videoAPI = mockVideoAPI(page);
    videoAPI.clearTasks();
  });

  test("shows three files on one Agent card and switches the current delivery", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "three-files",
          title: "三文件课件",
          agent_name: "ppt-master",
        },
      ],
      artifactSummaries: [
        summary("three-files", "delivered", [
          output("deck.pptx"),
          output("notes.txt"),
          output("outline.md"),
        ]),
      ],
    });

    await page.goto("/workspace/video-gen?tab=works&capability=ppt-master");
    await expect(page.getByTestId("work-card")).toHaveCount(1);
    await expect(page.getByTestId("work-card")).toContainText("3 个交付文件");
    await page.getByRole("button", { name: "查看", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByText("deck.pptx", { exact: true }).first(),
    ).toBeVisible();
    await dialog.getByRole("button", { name: /notes\.txt/ }).click();
    await expect(
      dialog.getByRole("button", { name: /notes\.txt/ }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(
      dialog.getByRole("link", { name: "下载当前文件" }),
    ).toHaveAttribute("href", /notes\.txt\?download=true/);
    await expect(dialog).not.toContainText("恢复版本");
  });

  test("keeps an undelivered conversation visible and continues its canonical route", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "empty-podcast",
          title: "未交付播客",
          agent_name: "podcast-studio",
        },
      ],
      artifactSummaries: [summary("empty-podcast", "not_delivered")],
    });

    await page.goto("/workspace/video-gen?tab=works&capability=podcast-studio");
    await expect(page.getByTestId("work-card")).toContainText("未交付");
    await expect(
      page.getByRole("button", { name: "查看", exact: true }),
    ).toHaveCount(0);
    await expect(page.locator("a[download]")).toHaveCount(0);
    await page.getByRole("button", { name: "继续对话", exact: true }).click();
    await expect(page).toHaveURL(
      /\/workspace\/agents\/podcast-studio\/chats\/empty-podcast$/,
    );
  });

  test("gates missing files within a partial delivery", async ({ page }) => {
    mockLangGraphAPI(page, {
      threads: [
        { thread_id: "partial", title: "部分交付", agent_name: "ppt-master" },
      ],
      artifactSummaries: [
        summary("partial", "partial", [
          output("available.pptx"),
          output("missing.pptx", {
            exists: false,
            size: null,
            modified_at: null,
          }),
        ]),
      ],
    });

    await page.goto("/workspace/video-gen?tab=works&capability=ppt-master");
    await expect(page.getByTestId("work-card")).toContainText("部分文件可用");
    await page.getByRole("button", { name: "查看", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: /missing\.pptx/ }).click();
    await expect(dialog).toContainText("文件当前不可用");
    await expect(
      dialog.getByRole("link", { name: "下载当前文件" }),
    ).toHaveCount(0);
    await expect(dialog.locator("video, audio, img")).toHaveCount(0);
  });

  test("keeps missing and unknown Agent works on continue without view or download", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "missing-agent",
          title: "缺失作品",
          agent_name: "creative-studio",
        },
        {
          thread_id: "unknown-agent",
          title: "未知作品",
          agent_name: "creative-studio",
        },
      ],
      artifactSummaries: [
        summary("missing-agent", "missing", [
          output("missing.png", {
            exists: false,
            size: null,
            modified_at: null,
          }),
        ]),
      ],
    });

    await page.goto(
      "/workspace/video-gen?tab=works&capability=creative-studio",
    );
    await expect(page.getByTestId("work-card")).toHaveCount(2);
    await expect(
      page.locator('[data-thread-id="unknown-agent"]'),
    ).toContainText("文件信息不可用");
    await expect(
      page.getByRole("button", { name: "查看", exact: true }),
    ).toHaveCount(0);
    await expect(page.locator("a[download]")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "继续对话", exact: true }),
    ).toHaveCount(2);
  });

  test("merges Agent and Remotion in course-video and AI video with ViMax", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "agent-course",
          title: "Agent 文字动效",
          agent_name: "course-video",
        },
      ],
      artifactSummaries: [
        summary("agent-course", "delivered", [output("agent.mp4")]),
      ],
    });
    videoAPI.seedTask({
      id: "remotion-course-task",
      title: "Remotion 课程",
      task_type: "remotion_course",
      status: "ready",
      progress: 100,
      delivery: {
        file_name: "course.mp4",
        file_size: 100,
        file_updated_at: "2026-11-02T09:00:00Z",
        media_kind: "video",
        exists: true,
      },
    });
    videoAPI.seedTask({
      id: "legacy-remotion-task",
      title: "旧 Remotion",
      task_type: "remotion",
      status: "ready",
      progress: 100,
    });
    videoAPI.seedTask({
      id: "ai-video-task",
      title: "AI 视频作品",
      task_type: "ai_video",
      status: "ready",
      progress: 100,
    });
    videoAPI.seedTask({
      id: "vimax-task",
      title: "ViMax 故事",
      task_type: "vimax",
      status: "ready",
      progress: 100,
    });

    await page.goto("/workspace/video-gen");
    await expect(page.locator('[data-thread-id="agent-course"]')).toBeVisible();
    await expect(taskCard(page, "remotion-course-task")).toBeVisible();
    await expect(taskCard(page, "legacy-remotion-task")).toBeVisible();
    await page.getByRole("tab", { name: "AI视频", exact: true }).click();
    await expect(taskCard(page, "ai-video-task")).toBeVisible();
    await expect(taskCard(page, "vimax-task")).toBeVisible();
  });

  test("preserves ready draft failed and missing task primary actions", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    videoAPI.seedTask({
      id: "ready-task",
      title: "就绪任务",
      task_type: "ai_video",
      status: "ready",
      progress: 100,
      delivery: {
        file_name: "ready.mp4",
        file_size: 100,
        file_updated_at: "2026-11-02T09:00:00Z",
        media_kind: "video",
        exists: true,
      },
    });
    videoAPI.seedTask({
      id: "draft-task",
      title: "草稿任务",
      task_type: "ai_video",
      status: "draft",
      progress: 100,
    });
    videoAPI.seedTask({
      id: "failed-task",
      title: "失败任务",
      task_type: "ai_video",
      status: "failed",
      progress: 70,
      error_message: "完整失败原因",
    });
    videoAPI.seedTask({
      id: "missing-task",
      title: "缺失任务",
      task_type: "ai_video",
      status: "ready",
      progress: 100,
      artifact_missing: true,
      thumbnail_url: "/api/video/tasks/missing-task/thumbnail",
      delivery: {
        file_name: "missing.mp4",
        file_size: null,
        file_updated_at: null,
        media_kind: "video",
        exists: false,
      },
    });

    await page.goto("/workspace/video-gen?tab=works&capability=ai_video");
    await expect(
      taskCard(page, "ready-task").getByRole("button", {
        name: "查看",
        exact: true,
      }),
    ).toBeEnabled();
    await expect(
      taskCard(page, "ready-task").locator("a[download]"),
    ).toHaveCount(1);
    await expect(
      taskCard(page, "draft-task").getByRole("button", {
        name: "继续编辑分镜",
      }),
    ).toBeEnabled();
    await expect(taskCard(page, "failed-task")).toContainText("完整失败原因");
    await taskCard(page, "failed-task")
      .getByRole("button", { name: "重试合成" })
      .click();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await page.getByRole("button", { name: "取消", exact: true }).click();
    await expect(taskCard(page, "missing-task")).toContainText("产物已清理");
    await expect(
      taskCard(page, "missing-task").getByRole("button", {
        name: "查看",
        exact: true,
      }),
    ).toBeDisabled();
    await expect(
      taskCard(page, "missing-task").locator("img, a[download]"),
    ).toHaveCount(0);
  });

  test("falls back to the existing download endpoint without delivery metadata", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    videoAPI.seedTask({
      id: "legacy-task",
      title: "旧后端作品",
      task_type: "ai_video",
      status: "ready",
      progress: 100,
    });

    await page.goto("/workspace/video-gen?tab=works&capability=ai_video");
    const card = taskCard(page, "legacy-task");
    await expect(card).toContainText("文件名由下载响应确定");
    await expect(card.locator("a[download]")).toHaveAttribute(
      "href",
      "/api/video/tasks/legacy-task/download",
    );
    await card.getByRole("button", { name: "查看", exact: true }).click();
    await expect(page.getByRole("dialog").locator("video")).toHaveAttribute(
      "src",
      "/api/video/tasks/legacy-task/download",
    );
  });

  test("searches loaded delivery file names", async ({ page }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "search-file",
          title: "标题无关",
          agent_name: "ppt-master",
        },
      ],
      artifactSummaries: [
        summary("search-file", "delivered", [output("Find-Me-Final.pptx")]),
      ],
    });

    await page.goto("/workspace/video-gen?tab=works&capability=ppt-master");
    await page
      .getByRole("searchbox", { name: "搜索作品标题与文件名" })
      .fill("find-me-final");
    await expect(page.getByTestId("work-card")).toHaveCount(1);
    await expect(page.getByTestId("work-card")).toContainText(
      "Find-Me-Final.pptx",
    );
  });

  test("keeps works after a refresh error and recovers on retry", async ({
    page,
  }) => {
    const api = mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "refresh-work",
          title: "刷新保留作品",
          agent_name: "ppt-master",
        },
      ],
      artifactSummaries: [
        summary("refresh-work", "delivered", [output("kept.pptx")]),
      ],
    });

    await page.goto("/workspace/video-gen?tab=works&capability=ppt-master");
    await expect(page.getByTestId("work-card")).toHaveCount(1);
    api.failArtifactSummaries(404);
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(
      page.locator("[data-testid='works-view'] [role='alert']"),
    ).toContainText("作品加载失败", { timeout: 15000 });
    await expect(page.getByTestId("work-card")).toHaveCount(1);
    api.clearArtifactSummaryFailure();
    await page.getByRole("button", { name: "重试", exact: true }).click();
    await expect(
      page.locator("[data-testid='works-view'] [role='alert']"),
    ).toHaveCount(0);
    await expect(page.getByTestId("work-card")).toHaveCount(1);
  });

  test("opens a legacy GIF as an image after video decoding fails", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    videoAPI.seedTask({
      id: "legacy-gif",
      title: "旧后端 GIF",
      task_type: "ai_video",
      status: "ready",
      progress: 100,
    });
    await page.route("**/api/video/tasks/legacy-gif/download", (route) =>
      route.fulfill({
        status: 200,
        contentType: "image/gif",
        body: Buffer.from(
          "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
          "base64",
        ),
      }),
    );

    await page.goto("/workspace/video-gen?tab=works&capability=ai_video");
    await taskCard(page, "legacy-gif")
      .getByRole("button", { name: "查看", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    const image = dialog.locator("img");
    await expect(image).toHaveAttribute(
      "src",
      "/api/video/tasks/legacy-gif/download",
    );
    await expect
      .poll(() =>
        image.evaluate((element) => (element as HTMLImageElement).naturalWidth),
      )
      .toBe(1);
  });

  test("does not invent versions for an overwritten same-name path", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "same-name",
          title: "同名文件",
          agent_name: "ppt-master",
        },
      ],
      artifactSummaries: [
        summary("same-name", "delivered", [output("current.pptx")]),
      ],
    });

    await page.goto("/workspace/video-gen?tab=works&capability=ppt-master");
    await expect(page.getByTestId("work-card")).toContainText("current.pptx");
    await expect(page.getByTestId("works-view")).not.toContainText("版本");
    await expect(page.getByTestId("works-view")).not.toContainText("恢复");
  });

  test("shows cancelled as a neutral non-spinning terminal state", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    videoAPI.seedTask({
      id: "cancelled-task",
      title: "已取消任务",
      task_type: "ai_video",
      status: "cancelled",
      progress: 45,
    });

    await page.goto("/workspace/video-gen?tab=works&capability=ai_video");
    const card = taskCard(page, "cancelled-task");
    await expect(card).toContainText("已取消");
    await expect(card).not.toContainText("排队中");
    const badge = card.getByTestId("work-status-badge");
    await expect(badge).toHaveClass(/bg-neutral-100/);
    await expect(badge.locator("svg")).not.toHaveClass(/animate-spin/);
    await expect(
      card.getByRole("button", { name: "查看", exact: true }),
    ).toBeDisabled();
  });

  test("avoids eager Agent media and unloads the selected viewer", async ({
    page,
  }) => {
    const threads = Array.from({ length: 6 }, (_, index) => ({
      thread_id: `media-${index}`,
      title: `媒体作品 ${index}`,
      agent_name: "creative-studio",
    }));
    const names = [
      "movie.mp4",
      "cover.png",
      "voice.mp3",
      "one.pptx",
      "two.pptx",
      "three.pptx",
    ];
    mockLangGraphAPI(page, {
      threads,
      artifactSummaries: threads.map((thread, index) =>
        summary(thread.thread_id, "delivered", [output(names[index]!)]),
      ),
      artifacts: {
        "/mnt/user-data/outputs/movie.mp4": "mock movie",
        "/mnt/user-data/outputs/cover.png": "mock image",
        "/mnt/user-data/outputs/voice.mp3": "mock audio",
      },
    });
    await page.route("**/api/threads/media-0/artifacts/**", (route) =>
      route.fulfill({
        status: 200,
        path: `${process.cwd()}/public/course-samples/image-motion.mp4`,
        contentType: "video/mp4",
      }),
    );
    // cover.png 回真实图片：夹具登记的文本正文会让 <img> 报错并回落到类型封面。
    await page.route("**/api/threads/media-1/artifacts/**", (route) =>
      route.fulfill({ status: 200, contentType: "image/png", body: PNG }),
    );
    const agentMediaRequests: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (
        url.pathname.includes("/api/threads/") &&
        url.pathname.includes("/artifacts/") &&
        /\.(mp4|mp3)$/.test(url.pathname)
      ) {
        agentMediaRequests.push(request.url());
      }
    });

    await page.goto(
      "/workspace/video-gen?tab=works&capability=creative-studio",
    );
    await expect(page.getByTestId("work-card")).toHaveCount(6);
    expect(agentMediaRequests).toEqual([]);
    await expect(
      page.locator('[data-thread-id="media-1"] img'),
    ).toHaveAttribute("loading", "lazy");
    await expect(page.locator("video, audio")).toHaveCount(0);

    const videoCard = page.locator('[data-thread-id="media-0"]');
    const viewButton = videoCard.getByRole("button", {
      name: "查看",
      exact: true,
    });
    await viewButton.click();
    const dialog = page.getByRole("dialog");
    const video = dialog.locator("video");
    await expect(video).toBeVisible();
    await expect.poll(() => agentMediaRequests.length).toBe(1);
    expect(new URL(agentMediaRequests[0]!).pathname).toContain(
      "/api/threads/media-0/artifacts/mnt/user-data/outputs/movie.mp4",
    );
    expect(
      await video.evaluate((element) => ({
        autoplay: (element as HTMLVideoElement).autoplay,
        controls: (element as HTMLVideoElement).controls,
      })),
    ).toEqual({ autoplay: false, controls: true });
    await expect(
      dialog.getByRole("link", { name: "下载当前文件" }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page.locator("video, audio")).toHaveCount(0);
    await expect(viewButton).toBeFocused();
  });

  test("blocks every missing-task media request", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [] });
    videoAPI.seedTask({
      id: "missing-network",
      title: "缺失媒体任务",
      task_type: "ai_video",
      status: "ready",
      progress: 100,
      artifact_missing: true,
      thumbnail_url: "/api/video/tasks/missing-network/thumbnail",
      delivery: {
        file_name: "missing.mp4",
        file_size: null,
        file_updated_at: null,
        media_kind: "video",
        exists: false,
      },
    });
    const requests: string[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (
        path === "/api/video/tasks/missing-network/thumbnail" ||
        path === "/api/video/tasks/missing-network/download"
      ) {
        requests.push(request.url());
      }
    });

    await page.goto("/workspace/video-gen?tab=works&capability=ai_video");
    const card = taskCard(page, "missing-network");
    await expect(card).toBeVisible();
    await expect(
      card.getByRole("button", { name: "查看", exact: true }),
    ).toBeDisabled();
    await expect(card.locator("img, video, audio, a[download]")).toHaveCount(0);
    expect(requests).toEqual([]);
  });

  for (const active of ["poster.html", "poster.svg"] as const) {
    test(`shows the delivered PNG as the cover of an ${active} work without fetching active content`, async ({
      page,
    }) => {
      const threadId = `poster-${active.endsWith("svg") ? "svg" : "html"}`;
      mockLangGraphAPI(page, {
        threads: [
          {
            thread_id: threadId,
            title: `课程海报 ${active}`,
            agent_name: "creative-studio",
          },
        ],
        artifactSummaries: [
          summary(threadId, "delivered", [
            output(active, { media_kind: "other" }),
            output("poster.png"),
          ]),
        ],
      });
      const artifactRequests = await serveOnlyPng(page, threadId);

      await page.goto(
        "/workspace/video-gen?tab=works&capability=creative-studio",
      );
      const card = page.locator(`[data-thread-id="${threadId}"]`);
      const cover = card.getByTestId("work-media").locator("img");
      await expect(cover).toHaveAttribute(
        "src",
        new RegExp(
          `/api/threads/${threadId}/artifacts/mnt/user-data/outputs/poster\\.png$`,
        ),
      );
      await expect(cover).toHaveAttribute("loading", "lazy");
      await expect(cover).toHaveAttribute("decoding", "async");
      await expect
        .poll(() =>
          cover.evaluate(
            (element) => (element as HTMLImageElement).naturalWidth,
          ),
        )
        .toBeGreaterThan(0);
      await expect(typeCover(card)).toHaveCount(0);
      // 卡片文件摘要仍是主交付文件（HTML/SVG）
      await expect(card).toContainText(active);

      // 「查看」默认打开主文件（HTML/SVG）的文件详情，不嵌入主动内容；下载仍指向主文件
      await card.getByRole("button", { name: "查看", exact: true }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await expect(typeCover(dialog)).toHaveAttribute(
        "data-cover-state",
        "default",
      );
      await expect(typeCover(dialog).locator("[data-cover-format]")).toHaveText(
        active.endsWith(".svg") ? "SVG" : "HTML",
      );
      await expect(dialog).toContainText("此文件类型暂不支持在线预览");
      await expect(dialog.locator("img, video, audio, iframe")).toHaveCount(0);
      await expect(
        dialog.getByRole("link", { name: "下载当前文件" }),
      ).toHaveAttribute("href", new RegExp(`${active}\\?download=true$`));

      // 列表阶段只请求了 PNG；HTML/SVG 正文零请求
      expect(
        artifactRequests.filter((path) => /\.(html|svg)$/i.test(path)),
      ).toEqual([]);
      expect(
        artifactRequests.filter((path) => /\.png$/i.test(path)),
      ).toHaveLength(1);
    });
  }

  test("falls back to type covers instead of broken images for works without a real thumbnail", async ({
    page,
  }) => {
    const agentCards = [
      ["course-video", "motion", "文字动效", "MP4", "lucide-clapperboard"],
      ["ppt-master", "deck", "PPT 课件", "PPTX", "lucide-presentation"],
      ["podcast-studio", "podcast", "语音播客", "MP3", "lucide-audio-lines"],
    ] as const;
    mockLangGraphAPI(page, {
      threads: agentCards.map(([capability, id, title]) => ({
        thread_id: id,
        title,
        agent_name: capability,
      })),
      artifactSummaries: [
        summary("motion", "delivered", [output("lesson.mp4")]),
        summary("deck", "delivered", [output("slides.pptx")]),
        summary("podcast", "delivered", [
          output("episode.mp3", { media_kind: "audio" }),
        ]),
      ],
    });
    videoAPI.seedTask({
      id: "no-thumb",
      title: "无缩略图任务",
      task_type: "ai_video",
      status: "ready",
      progress: 100,
      thumbnail_url: null,
      delivery: readyDelivery("no-thumb.mp4"),
    });
    videoAPI.seedTask({
      id: "broken-thumb",
      title: "缩略图损坏任务",
      task_type: "ai_video",
      status: "ready",
      progress: 100,
      thumbnail_url: "/api/video/tasks/broken-thumb/thumbnail",
      delivery: readyDelivery("broken.mp4"),
    });
    await page.route("**/api/video/tasks/broken-thumb/thumbnail", (route) =>
      route.fulfill({ status: 404, body: "" }),
    );

    for (const [capability, id, , badge, icon] of agentCards) {
      await page.goto(
        `/workspace/video-gen?tab=works&capability=${capability}`,
      );
      const card = page.locator(`[data-thread-id="${id}"]`);
      await expect(typeCover(card)).toHaveAttribute(
        "data-cover-state",
        "default",
      );
      await expect(typeCover(card).locator("[data-cover-format]")).toHaveText(
        badge,
      );
      await expect(typeCover(card).locator(`svg.${icon}`)).toHaveCount(1);
      await expect(card.locator("img, video, audio")).toHaveCount(0);
      await expect(card.locator("[src]")).toHaveCount(0);
    }

    await page.goto("/workspace/video-gen?tab=works&capability=ai_video");
    await expect(
      typeCover(taskCard(page, "no-thumb")).locator("[data-cover-format]"),
    ).toHaveText("MP4");
    await expect(taskCard(page, "no-thumb").locator("img")).toHaveCount(0);
    // 缩略图 URL 存在但 404：先尝试加载，失败后回落到类型封面，不留破图
    const broken = taskCard(page, "broken-thumb");
    await expect(typeCover(broken)).toHaveAttribute(
      "data-cover-state",
      "default",
    );
    await expect(broken.locator("img")).toHaveCount(0);
    await expect(broken).toContainText("AI 生视频");
  });

  test("keeps the works list free of media, active-content and download requests", async ({
    page,
  }) => {
    const threads = [
      {
        thread_id: "gate-motion",
        title: "文字动效",
        agent_name: "course-video",
      },
      { thread_id: "gate-deck", title: "PPT 课件", agent_name: "ppt-master" },
      {
        thread_id: "gate-podcast",
        title: "语音播客",
        agent_name: "podcast-studio",
      },
      {
        thread_id: "gate-poster",
        title: "课程海报",
        agent_name: "creative-studio",
      },
    ];
    mockLangGraphAPI(page, {
      threads,
      artifactSummaries: [
        summary("gate-motion", "delivered", [output("lesson.mp4")]),
        summary("gate-deck", "delivered", [output("slides.pptx")]),
        summary("gate-podcast", "delivered", [
          output("episode.mp3", { media_kind: "audio" }),
        ]),
        summary("gate-poster", "delivered", [
          output("poster.html", { media_kind: "other" }),
          output("poster.png"),
        ]),
      ],
    });
    videoAPI.seedTask({
      id: "gate-task",
      title: "任务视频",
      task_type: "ai_video",
      status: "ready",
      progress: 100,
      thumbnail_url: "/api/video/tasks/gate-task/thumbnail",
      delivery: readyDelivery("gate.mp4"),
    });
    const artifactRequests = await serveOnlyPng(page, "gate-poster");
    const mediaRequests: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      // 只看 API 请求：/images/*.svg 之类的静态资源不在门禁范围
      if (!url.pathname.startsWith("/api/")) return;
      if (
        /\.(mp4|mp3|html|svg)(\?|$)/i.test(url.pathname + url.search) ||
        url.searchParams.has("download") ||
        /\/download(\?|$)/.test(url.pathname + url.search)
      ) {
        mediaRequests.push(url.pathname + url.search);
      }
    });

    for (const capability of [
      "course-video",
      "ppt-master",
      "podcast-studio",
      "creative-studio",
    ]) {
      await page.goto(
        `/workspace/video-gen?tab=works&capability=${capability}`,
      );
      await expect(page.getByTestId("work-card")).toHaveCount(1);
    }
    // 列表阶段只允许真实图片封面（PNG）与任务缩略图请求
    await expect(
      page.locator('[data-thread-id="gate-poster"] img'),
    ).toHaveAttribute("loading", "lazy");
    await expect.poll(() => artifactRequests.length).toBe(1);
    await page.goto("/workspace/video-gen?tab=works&capability=ai_video");
    await expect(page.locator('[data-task-id="gate-task"] img')).toHaveCount(1);

    expect(mediaRequests).toEqual([]);
    expect(artifactRequests).toEqual([
      "/api/threads/gate-poster/artifacts/mnt/user-data/outputs/poster.png",
    ]);
  });

  test("rejects invalid summary batches like the backend", async ({ page }) => {
    mockLangGraphAPI(page);
    await page.goto("/");
    const statuses = await page.evaluate(async () => {
      const post = async (threadIds: string[]) =>
        (
          await fetch("/api/works/artifact-summaries", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ thread_ids: threadIds }),
          })
        ).status;
      return Promise.all([
        post([]),
        post(Array.from({ length: 51 }, (_, index) => `id-${index}`)),
        post(["  "]),
        post(["x".repeat(129)]),
      ]);
    });

    expect(statuses).toEqual([422, 422, 422, 422]);
  });

  test("keeps capability tabs keyboard accessible", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [] });
    await page.goto("/workspace/video-gen");
    const courseTab = page.getByRole("tab", { name: "文字动效", exact: true });
    await courseTab.focus();
    await page.keyboard.press("End");
    await expect(
      page.getByRole("tab", { name: "语音播客", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Home");
    await expect(courseTab).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("ArrowRight");
    await expect(
      page.getByRole("tab", { name: "图片动效", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
  });

  test("renders English delivery copy in dark mode", async ({ page }) => {
    await page
      .context()
      .addCookies([
        { name: "locale", value: "en-US", domain: "localhost", path: "/" },
      ]);
    await page.addInitScript(() => localStorage.setItem("theme", "dark"));
    await page.emulateMedia({ colorScheme: "dark" });
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: "english-dark",
          title: "English deck",
          agent_name: "ppt-master",
        },
        {
          thread_id: "english-poster",
          title: "English poster",
          agent_name: "creative-studio",
        },
      ],
      artifactSummaries: [
        summary("english-dark", "delivered", [output("english.pptx")]),
        summary("english-poster", "delivered", [
          output("poster.html", { media_kind: "other" }),
          output("poster.png"),
        ]),
      ],
    });
    await serveOnlyPng(page, "english-poster");
    videoAPI.seedTask({
      id: "english-task",
      title: "English task",
      task_type: "ai_video",
      status: "ready",
      progress: 100,
      thumbnail_url: "/api/video/tasks/english-task/thumbnail",
      delivery: readyDelivery("english.mp4"),
    });

    // 类型封面（纯 PPTX）在深色下的观感
    await page.goto("/workspace/video-gen?tab=works&capability=ppt-master");
    await expect(
      page.getByRole("heading", { name: "My works", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/View and download delivery files/),
    ).toBeVisible();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(
      typeCover(page.getByTestId("work-card")).locator("[data-cover-format]"),
    ).toHaveText("PPTX");
    await page.screenshot({
      path: test.info().outputPath("english-dark-type-cover.png"),
      fullPage: true,
      animations: "disabled",
    });
    await page.getByRole("button", { name: "View", exact: true }).click();
    await expect(
      page.getByRole("link", { name: "Download current file" }),
    ).toBeVisible();
    await expect(typeCover(page.getByRole("dialog"))).toBeVisible();
    await page.screenshot({
      path: test.info().outputPath("english-dark.png"),
      fullPage: true,
      animations: "disabled",
    });

    // 真实图片封面（同作品交付的 PNG）与任务缩略图在深色下的观感
    await page.goto(
      "/workspace/video-gen?tab=works&capability=creative-studio",
    );
    const posterCover = page
      .locator(
        '[data-thread-id="english-poster"] [data-testid="work-media"] img',
      )
      .first();
    await expect
      .poll(() =>
        posterCover.evaluate(
          (element) => (element as HTMLImageElement).naturalWidth,
        ),
      )
      .toBeGreaterThan(0);
    await page.screenshot({
      path: test.info().outputPath("english-dark-real-cover.png"),
      fullPage: true,
      animations: "disabled",
    });
    await page.goto("/workspace/video-gen?tab=works&capability=ai_video");
    await expect(
      taskCard(page, "english-task").locator("[data-testid='work-media'] img"),
    ).toHaveCount(1);
  });
});
