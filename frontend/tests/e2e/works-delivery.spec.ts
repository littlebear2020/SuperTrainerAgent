import { expect, type Page, test } from "@playwright/test";

import {
  mockLangGraphAPI,
  type MockArtifactFile,
  type MockArtifactSummary,
} from "./utils/mock-api";
import { mockVideoAPI, type MockVideoAPI } from "./utils/mock-video-api";

let videoAPI: MockVideoAPI;

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
        summary("unknown-agent", "unknown"),
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
});
