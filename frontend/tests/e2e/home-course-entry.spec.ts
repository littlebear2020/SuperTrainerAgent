import { expect, test } from "@playwright/test";

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
    for (const [id, agent] of [
      ["course-video", "course-video"],
      ["creative-studio", "creative-studio"],
      ["ppt-master", "ppt-master"],
      ["co-writer", "co-writer"],
    ] as const) {
      await page.goto("/workspace");
      if (["creative-studio", "ppt-master", "co-writer"].includes(id)) {
        await page.getByRole("button", { name: /图文课/ }).click();
      }
      await page.locator(`[data-course-entry='${id}']`).click();
      await expect(page).toHaveURL(`/workspace/agents/${agent}/chats/new`);
    }

    await page.goto("/workspace");
    await page.locator("[data-course-entry='ai_image']").click();
    let dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("combobox").first()).toHaveText("AI 生图");
    await dialog.getByRole("button", { name: "取消" }).click();
    await page.locator("[data-course-entry='ai_video']").click();
    dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("combobox").first()).toHaveText("AI 生视频");
  });

  test("alternating AI entries starts clean after close", async ({ page }) => {
    await page.goto("/workspace");
    await page.locator("[data-course-entry='ai_image']").click();
    let dialog = page.getByRole("dialog");
    await dialog.getByPlaceholder("留空则取脚本前 30 字").fill("残留标题");
    await dialog
      .getByPlaceholder(/每句话对应一个画面/)
      .fill("不应残留的旧脚本");
    await dialog.getByRole("button", { name: /高级设置/ }).click();
    await dialog.getByRole("checkbox", { name: "跟随默认" }).uncheck();
    await dialog.getByRole("button", { name: "取消" }).click();

    await page.locator("[data-course-entry='ai_video']").click();
    dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("combobox").first()).toHaveText("AI 生视频");
    await expect(dialog.getByPlaceholder("留空则取脚本前 30 字")).toHaveValue(
      "",
    );
    await expect(dialog.getByPlaceholder(/每句话对应一个画面/)).toHaveValue("");
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
    await expect(dialog.getByRole("combobox").first()).toHaveText("AI 生图");
    await expect(dialog.getByPlaceholder("留空则取脚本前 30 字")).toHaveValue(
      "",
    );
    await expect(dialog.getByPlaceholder(/每句话对应一个画面/)).toHaveValue("");
    await expect(
      dialog.getByRole("checkbox", { name: "跟随默认" }),
    ).toHaveCount(0);
  });

  test("only expanded panel plays and reduced motion stops playback", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      const target = window as unknown as {
        __mediaCalls: { kind: string; src: string }[];
      };
      target.__mediaCalls = [];
      // eslint-disable-next-line @typescript-eslint/unbound-method
      const play = HTMLMediaElement.prototype.play;
      // eslint-disable-next-line @typescript-eslint/unbound-method
      const pause = HTMLMediaElement.prototype.pause;
      HTMLMediaElement.prototype.play = function () {
        target.__mediaCalls.push({
          kind: "play",
          src: this.currentSrc || this.src,
        });
        return play.call(this);
      };
      HTMLMediaElement.prototype.pause = function () {
        target.__mediaCalls.push({
          kind: "pause",
          src: this.currentSrc || this.src,
        });
        return pause.call(this);
      };
    });
    await page.goto("/workspace");
    const calls = () =>
      page.evaluate(() =>
        (
          window as unknown as { __mediaCalls: { kind: string }[] }
        ).__mediaCalls.map((call) => call.kind),
      );
    await expect
      .poll(
        async () => (await calls()).filter((kind) => kind === "play").length,
      )
      .toBe(3);
    await page.getByRole("button", { name: /图文课/ }).click();
    await expect
      .poll(
        async () => (await calls()).filter((kind) => kind === "pause").length,
      )
      .toBeGreaterThanOrEqual(3);
    expect(await page.locator("video").count()).toBe(0);
    await page.getByRole("button", { name: /视频课/ }).click();
    await expect
      .poll(
        async () => (await calls()).filter((kind) => kind === "play").length,
      )
      .toBe(6);

    const playCount = (await calls()).filter((kind) => kind === "play").length;
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect
      .poll(
        async () => (await calls()).filter((kind) => kind === "pause").length,
      )
      .toBeGreaterThanOrEqual(6);
    expect((await calls()).filter((kind) => kind === "play")).toHaveLength(
      playCount,
    );
    expect(
      await page
        .locator("video")
        .evaluateAll((videos) =>
          videos.every((video) => (video as HTMLVideoElement).paused),
        ),
    ).toBe(true);
  });

  test("sample playback failure keeps the creation action usable", async ({
    page,
  }) => {
    await page.route("**/course-samples/remotion.mp4", (route) =>
      route.abort(),
    );
    await page.goto("/workspace");
    await expect(page.getByText("视频暂时无法播放")).toBeVisible();
    await page.locator("[data-course-entry='course-video']").click();
    await expect(page).toHaveURL("/workspace/agents/course-video/chats/new");
  });

  test("mobile home has no horizontal overflow and keeps both entries operable", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/workspace");
    await expect(page.locator("[data-course-entry]")).toHaveCount(3);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: /图文课/ }).click();
    for (const id of ["creative-studio", "ppt-master", "co-writer"]) {
      await expect(page.locator(`[data-course-entry='${id}']`)).toBeVisible();
    }
    await page.getByRole("button", { name: /视频课/ }).click();
    await page.locator("[data-course-entry='ai_image']").click();
    await expect(page.getByRole("dialog")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
  });
});
