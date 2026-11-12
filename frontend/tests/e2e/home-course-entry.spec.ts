import { expect, test } from "@playwright/test";

import { pinEnglishLocale } from "./utils/locale";
import { mockLangGraphAPI } from "./utils/mock-api";
import { mockVideoAPI } from "./utils/mock-video-api";

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

  test("initial media uses posters and every static image is contained", async ({
    page,
  }) => {
    await page.goto("/workspace");
    await expect(page.locator("video")).toHaveCount(0);
    for (const [id, poster] of [
      ["course-video", "/course-samples/text-motion-poster.webp"],
      ["ai_image", "/course-samples/image-motion-poster.webp"],
      ["ai_video", "/course-samples/ai-video-poster.webp"],
    ] as const) {
      const image = page.locator(`[data-course-entry='${id}'] img`);
      await expect(image).toHaveAttribute("src", poster);
    }
    for (const [id, src] of [
      ["creative-studio", "/course-samples/knowledge-card.webp"],
      ["ppt-master", "/course-samples/ppt-work.webp"],
      ["podcast-studio", "/course-samples/podcast-work.webp"],
    ] as const) {
      const image = page.locator(`[data-course-entry='${id}'] img`);
      await expect(image).toHaveAttribute("src", src);
      expect(
        await image.evaluate((element) => getComputedStyle(element).objectFit),
      ).toBe("contain");
    }
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

  test("hover and focus mount only one preview and leaving unmounts it", async ({
    page,
  }) => {
    await page.goto("/workspace");
    const card = page.locator("[data-course-entry='course-video']");
    await expect(page.locator("video")).toHaveCount(0);
    await card.hover();
    await expect(page.locator("video")).toHaveCount(0);
    await expect
      .poll(() => page.locator("video").count(), { timeout: 2_000 })
      .toBe(1);
    await expect
      .poll(() =>
        page
          .locator("video")
          .evaluate((element) => (element as HTMLVideoElement).currentTime),
      )
      .toBeGreaterThan(0.05);
    await page.locator("body").click({ position: { x: 0, y: 0 } });
    await expect(page.locator("video")).toHaveCount(0);

    await card.focus();
    await expect(page.locator("video")).toHaveCount(1);
    await page.locator("body").click({ position: { x: 0, y: 0 } });
    await expect(page.locator("video")).toHaveCount(0);
  });

  test("reduced motion keeps poster and disables preview", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/workspace");
    await page.locator("[data-course-entry='ai_image']").focus();
    await expect(page.locator("video")).toHaveCount(0);
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
    await page.route("**/course-samples/remotion.mp4", (route) =>
      route.abort(),
    );
    await page.route("**/course-samples/knowledge-card.webp", (route) =>
      route.abort(),
    );
    await page.goto("/workspace");
    await page.locator("[data-course-entry='course-video']").focus();
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

  test("desktop and mobile Chinese screenshots and English copy", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/workspace");
    await expect(page.locator("[data-course-entry]")).toHaveCount(6);
    await page.waitForFunction(() =>
      Array.from(document.images).every((image) => image.complete),
    );
    await page.screenshot({
      path: "../temp/workspace-experience-2026-11-12/task5-desktop-zh.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: "../temp/workspace-experience-2026-11-12/task5-mobile-zh.png",
      fullPage: true,
    });

    await pinEnglishLocale(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/workspace");
    await expect(
      page.getByText("Great content starts with one idea."),
    ).toBeVisible();
    await page.screenshot({
      path: "../temp/workspace-experience-2026-11-12/task5-desktop-en.png",
      fullPage: true,
    });
  });
});
