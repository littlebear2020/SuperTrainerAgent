import { expect, test, type Page } from "@playwright/test";

import { pinEnglishLocale } from "./utils/locale";
import { mockLangGraphAPI } from "./utils/mock-api";
import { mockVideoAPI } from "./utils/mock-video-api";

const VIDEO_IDS = ["course-video", "ai_image", "ai_video"] as const;
const ENTRY_IDS = [
  ...VIDEO_IDS,
  "creative-studio",
  "ppt-master",
  "podcast-studio",
] as const;

async function ambientAnimationState(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const ambient = document.querySelector(".ws-ambient");
    const cyan = document.querySelector(".ws-ambient-orb-cyan");
    return [
      ambient
        ? getComputedStyle(ambient, "::before").animationName
        : "unmounted",
      ambient
        ? getComputedStyle(ambient, "::after").animationName
        : "unmounted",
      cyan ? getComputedStyle(cyan).animationName : "unmounted",
    ];
  });
}

async function expectVideosPlaying(page: Page) {
  await expect(page.locator("video")).toHaveCount(3);
  for (const id of VIDEO_IDS) {
    const video = page.locator(
      `[data-course-entry='${id}'] video[data-course-item='${id}']`,
    );
    await expect(video).toBeVisible();
    expect(
      await video.evaluate((element) => {
        const media = element as HTMLVideoElement;
        return {
          autoplay: media.autoplay,
          muted: media.muted,
          loop: media.loop,
          playsInline: media.playsInline,
        };
      }),
    ).toEqual({ autoplay: true, muted: true, loop: true, playsInline: true });
    await expect
      .poll(() =>
        video.evaluate((element) => (element as HTMLVideoElement).currentTime),
      )
      .toBeGreaterThan(0.05);
    expect(
      await video.evaluate((element) => (element as HTMLVideoElement).paused),
    ).toBe(false);
  }
}

let pageErrors: string[];

test.describe("home course entries", () => {
  test.beforeEach(async ({ page }) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    mockLangGraphAPI(page);
    mockVideoAPI(page);
  });

  test.afterEach(() => {
    expect(pageErrors).toEqual([]);
  });

  test("six confirmed entries open the right agent or AI dialog", async ({
    page,
  }) => {
    await page.goto("/workspace");
    await expect(page.locator("[data-course-entry]")).toHaveCount(6);
    await expect(page.getByText("近期项目")).toHaveCount(0);
    for (const [id, agent] of [
      ["course-video", "course-video"],
      ["creative-studio", "creative-studio"],
      ["ppt-master", "ppt-master"],
      ["podcast-studio", "podcast-studio"],
    ] as const) {
      await page.locator(`[data-course-entry='${id}']`).click();
      await expect(page).toHaveURL(`/workspace/agents/${agent}/chats/new`);
      await page.goto("/workspace");
    }

    await page.locator("[data-course-entry='ai_image']").click();
    let dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("heading", { name: "创建图片动效" }),
    ).toBeVisible();
    await expect(dialog.getByText("视频类型")).toHaveCount(0);
    await expect(dialog.getByText("话题生成")).toHaveCount(0);
    await dialog.getByRole("button", { name: "取消" }).click();
    await page.locator("[data-course-entry='ai_video']").click();
    dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("heading", { name: "创建 AI 动态视频" }),
    ).toBeVisible();
  });

  test("two groups show final names, media labels and approved tag counts", async ({
    page,
  }) => {
    await page.goto("/workspace");
    await expect(page.locator("[data-course-group]")).toHaveCount(2);
    for (const copy of [
      "视频课",
      "VIDEO CREATION",
      "图文音频课",
      "DESIGN & AUDIO",
    ]) {
      await expect(page.getByText(copy, { exact: true })).toBeVisible();
    }
    await expect(page.getByText("03", { exact: true })).toHaveCount(0);
    for (const title of [
      "文字动效",
      "图片动效",
      "AI视频",
      "课程海报",
      "PPT课件",
      "语音播客",
    ]) {
      await expect(page.getByText(title, { exact: true })).toBeVisible();
    }
    for (const label of [
      "文稿→动态讲解",
      "文稿→动态图片",
      "文稿→动态镜头",
      "主题→视觉海报",
      "资料→演示文稿",
      "资料→语音播客",
    ]) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
    for (const [index, id] of ENTRY_IDS.entries()) {
      await expect(
        page.locator(`[data-course-entry='${id}'] [data-course-tag]`),
      ).toHaveCount([3, 3, 3, 3, 3, 4][index]!);
    }
    for (const removed of [
      "数据变动态图表",
      "流程变信息图",
      "文档精编成课件",
      "精密工程",
      "纸艺拼贴",
    ]) {
      await expect(page.getByText(removed, { exact: true })).toHaveCount(0);
    }
  });

  test("all three videos autoplay and every static image is covered", async ({
    page,
  }) => {
    await page.goto("/workspace");
    await expectVideosPlaying(page);
    for (const [id, src] of [
      ["course-video", "/course-samples/text-motion-preview.mp4"],
      ["ai_image", "/course-samples/image-motion.mp4"],
      ["ai_video", "/course-samples/ai-video.mp4"],
    ] as const) {
      await expect(
        page.locator(`[data-course-entry='${id}'] video`),
      ).toHaveAttribute("src", src);
    }
    for (const [id, src] of [
      ["creative-studio", "/course-samples/poster-design.svg"],
      ["ppt-master", "/course-samples/ppt-design.svg"],
      ["podcast-studio", "/course-samples/podcast-work.webp"],
    ] as const) {
      const image = page.locator(`[data-course-entry='${id}'] img`);
      await expect(image).toHaveAttribute("src", src);
      expect(
        await image.evaluate((element) => getComputedStyle(element).objectFit),
      ).toBe("cover");
    }
    expect(
      await page
        .locator("[data-course-entry] img")
        .evaluateAll((elements) =>
          elements.every(
            (element) => getComputedStyle(element).objectFit === "cover",
          ),
        ),
    ).toBe(true);
  });

  test("desktop grid has three columns and equal-height rows", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/workspace");
    const cards = page.locator("[data-course-entry]");
    await expect(cards).toHaveCount(6);
    const heights = await cards.evaluateAll((elements) =>
      elements.map((element) => element.getBoundingClientRect().height),
    );
    expect(
      Math.max(...heights.slice(0, 3)) - Math.min(...heights.slice(0, 3)),
    ).toBeLessThanOrEqual(2);
    expect(
      Math.max(...heights.slice(3)) - Math.min(...heights.slice(3)),
    ).toBeLessThanOrEqual(2);
    const columns = await cards
      .first()
      .evaluate(
        (element) =>
          getComputedStyle(element.parentElement!).gridTemplateColumns,
      );
    expect(columns.split(" ")).toHaveLength(3);
  });

  test("hover does not change playback and visibility restores all videos", async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.goto("/workspace");
    await expectVideosPlaying(page);
    await page.locator("[data-course-entry='course-video']").hover();
    await expect(page.locator("video")).toHaveCount(3);
    await page.locator("body").click({ position: { x: 0, y: 0 } });
    await expect(page.locator("video")).toHaveCount(3);
    await expect
      .poll(() => ambientAnimationState(page), { timeout: 2_000 })
      .toEqual(["ws-ambient-drift", "ws-orb-drift-2", "ws-orb-drift-3"]);

    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", {
        configurable: true,
        value: true,
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(page.locator("video")).toHaveCount(0);
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", {
        configurable: true,
        value: false,
      });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expectVideosPlaying(page);
  });

  test("reduced motion keeps posters and disables all ambient animation", async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/workspace");
    await expect(page.locator("video")).toHaveCount(0);
    await expect(page.locator("[data-course-entry] img:visible")).toHaveCount(
      6,
    );
    await expect(page.locator("[data-media-status]")).toHaveCount(0);
    expect(await ambientAnimationState(page)).toEqual([
      "unmounted",
      "unmounted",
      "unmounted",
    ]);
  });

  test.describe("real coarse pointer context", () => {
    test.use({ hasTouch: true });

    test("coarse pointer disables decoration but keeps videos playing", async ({
      page,
    }) => {
      await page.emulateMedia({ reducedMotion: "no-preference" });
      await page.goto("/workspace");
      expect(
        await page.evaluate(
          () => window.matchMedia("(pointer: coarse)").matches,
        ),
      ).toBe(true);
      expect(await ambientAnimationState(page)).toEqual([
        "unmounted",
        "unmounted",
        "unmounted",
      ]);
      await expectVideosPlaying(page);
    });
  });

  test("play rejection keeps the poster and creation available", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      HTMLMediaElement.prototype.play = function () {
        if (this.dataset.courseItem === "course-video") {
          return Promise.reject(
            new DOMException("Playback blocked", "NotAllowedError"),
          );
        }
        return Promise.resolve();
      };
    });
    await page.goto("/workspace");
    const card = page.locator("[data-course-entry='course-video']");
    await expect(card.getByText("预览暂不可用，仍可开始创作")).toBeVisible();
    await expect(card.locator("img")).toBeVisible();
    await expect(card.locator("video")).toHaveCount(0);
    await expect(page.locator("video")).toHaveCount(2);
    await card.click();
    await expect(page).toHaveURL("/workspace/agents/course-video/chats/new");
  });

  test("a rejected video can retry and still open creation", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      let rejected = false;
      HTMLMediaElement.prototype.play = function () {
        if (this.dataset.courseItem === "course-video" && !rejected) {
          rejected = true;
          return Promise.reject(
            new DOMException("First failure", "NotAllowedError"),
          );
        }
        return Promise.resolve();
      };
    });
    await page.goto("/workspace");
    const card = page.locator("[data-course-entry='course-video']");
    await expect(card.getByText("预览暂不可用，仍可开始创作")).toBeVisible();
    await page.locator("[data-course-retry='course-video']").click();
    await expect(card.getByText("预览暂不可用，仍可开始创作")).toHaveCount(0);
    await expect(card.locator("video")).toBeVisible();
    await card.click();
    await expect(page).toHaveURL("/workspace/agents/course-video/chats/new");
  });

  test("alternating AI entries starts clean after close", async ({ page }) => {
    await page.goto("/workspace");
    await page.locator("[data-course-entry='ai_image']").click();
    let dialog = page.getByRole("dialog");
    await dialog.getByPlaceholder("留空则取脚本前 30 字").fill("残留标题");
    await dialog
      .getByPlaceholder(/每一句对应一个画面或镜头/)
      .fill("不应残留的旧脚本");
    await dialog.getByRole("button", { name: /高级设置/ }).click();
    await dialog.getByRole("checkbox", { name: "跟随默认" }).uncheck();
    await dialog.getByRole("button", { name: "取消" }).click();

    await page.locator("[data-course-entry='ai_video']").click();
    dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("heading", { name: "创建 AI 动态视频" }),
    ).toBeVisible();
    await expect(dialog.getByPlaceholder("留空则取脚本前 30 字")).toHaveValue(
      "",
    );
    await expect(
      dialog.getByPlaceholder(/每一句对应一个画面或镜头/),
    ).toHaveValue("");
    await expect(
      dialog.getByRole("checkbox", { name: "跟随默认" }),
    ).toHaveCount(0);
    await dialog.getByRole("button", { name: /高级设置/ }).click();
    await expect(
      dialog.getByRole("checkbox", { name: "跟随默认" }),
    ).toBeChecked();
    await dialog.getByRole("button", { name: "取消" }).click();

    await page.locator("[data-course-entry='ai_image']").click();
    dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("heading", { name: "创建图片动效" }),
    ).toBeVisible();
    await expect(dialog.getByText("视频类型")).toHaveCount(0);
    await expect(dialog.getByText("话题生成")).toHaveCount(0);
    await expect(dialog.getByPlaceholder("留空则取脚本前 30 字")).toHaveValue(
      "",
    );
    await expect(
      dialog.getByRole("checkbox", { name: "跟随默认" }),
    ).toHaveCount(0);
  });

  test("media failures preserve creation and neutral fallbacks", async ({
    page,
  }) => {
    await page.route("**/course-samples/text-motion-preview.mp4", (route) =>
      route.abort(),
    );
    await page.route("**/course-samples/poster-design.svg", (route) =>
      route.abort(),
    );
    await page.goto("/workspace");
    await expect(page.getByText("预览暂不可用，仍可开始创作")).toBeVisible();
    await expect(
      page.locator("[data-course-entry='creative-studio'] img"),
    ).toHaveCount(0);
    await page.locator("[data-course-entry='creative-studio']").click();
    await expect(page).toHaveURL("/workspace/agents/creative-studio/chats/new");
  });

  test("390px and 320px mobile have no horizontal overflow", async ({
    page,
  }) => {
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/workspace");
      await expect(page.locator("[data-course-entry]")).toHaveCount(6);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBe(true);
    }
    await page.locator("[data-course-entry='ai_image']").click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("focused AI dialog uses an opaque surface", async ({ page }) => {
    await page.goto("/workspace");
    await page.locator("[data-course-entry='ai_image']").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    const surface = await dialog.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        backgroundColor: style.backgroundColor,
        backgroundImage: style.backgroundImage,
      };
    });
    expect(surface.backgroundColor).not.toBe("rgba(0, 0, 0, 0)");
    expect(surface.backgroundImage).toBe("none");
    await expect
      .poll(() =>
        dialog.evaluate((element) => getComputedStyle(element).opacity),
      )
      .toBe("1");
  });

  test("desktop and mobile Chinese and English screenshots", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 1600 });
    await page.goto("/workspace");
    await expectVideosPlaying(page);
    await page.waitForFunction(() =>
      Array.from(document.images).every((image) => image.complete),
    );
    await page.screenshot({
      path: "../temp/workspace-home-final-design/desktop-zh.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: "../temp/workspace-home-final-design/mobile-zh.png",
      fullPage: true,
    });

    await pinEnglishLocale(page);
    await page.setViewportSize({ width: 1440, height: 1600 });
    await page.goto("/workspace");
    await expect(
      page.getByText("Great content starts with one idea."),
    ).toBeVisible();
    await expectVideosPlaying(page);
    await page.waitForFunction(() =>
      Array.from(document.images).every((image) => image.complete),
    );
    await page.screenshot({
      path: "../temp/workspace-home-final-design/desktop-en.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: "../temp/workspace-home-final-design/mobile-en.png",
      fullPage: true,
    });
  });
});
