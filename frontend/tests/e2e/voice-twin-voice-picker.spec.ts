/**
 * E2E: voice-twin production voice picker (Popover) — open, preview, select.
 *
 * Mocks backend voice APIs via page.route() (no real TTS / credentials).
 * Requires DEER_FLOW_AUTH_DISABLED=1 (see tests/e2e/video-gen.spec.ts header).
 */
import { expect, test } from "@playwright/test";

// Minimal silent MP3 (base64) so <audio>.play() resolves in headless Chromium.
const SILENT_MP3_B64 =
  "SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQwAADB8AhSmxhIIEVCSiJrDCQBTcu3UrAIwUdkRgQbFAZC1CQEwTJ9mjRvBA4UOLD8nKVOWfh+UlK4E6mgBFq/WGoRgiCgSPdfhjwBBSL+pmDDv7QFXfRUYvfFFJpiQu54dEvLDDtjK5FzbZUizF3CDLKtwODrkPRElmIIfjhnLyBCjByJpKppxQZKQAQUQ/Y+zNQyTH/fjMHE0fWXtYX3hxBQKVxl2QhqFlNFFujN0ByTaSbjfzPa2QyGKvz+V0p9//y/////8QXBAAAbB0AAAM/wAAABlAQBFnQAAdDQAAACrAKlvAAACFEAwHBAAZAcBAAAf2AQAAAAkI6QAAAgTgMAAB5FEBAACeQAQAA6OAAAACkDiVJAAACw0AAAAdFBmZ6QUAAAD1AQAAAGkGpGaSAAACy0AAAAA9AAAAAGkHtZ6eAAAC20AAAABBHVn6BwAAAMlAAACXAzYAAACRAAAAAAEHAfYAAAAtAAAAAACRAEYAAADJ";

test("voice picker opens, plays a preview, and selects a voice", async ({
  page,
}) => {
  let previewHit = false;

  await page.route("**/api/voice-twins**", async (route) => {
    const url = route.request().url();
    // Order matters: specific routes first, bare list last.
    if (url.includes("/preset-voices")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "preset:146",
            name: "知性女声",
            gender: "female",
            locale: "zh-CN",
            provider: "qihu",
            description: "专业知性",
            scenario: ["培训讲解"],
            tone: ["知性"],
          },
        ]),
      });
    }
    if (url.includes("/voices/") && url.includes("/preview")) {
      previewHit = true;
      return route.fulfill({
        status: 200,
        contentType: "audio/mpeg",
        body: Buffer.from(SILENT_MP3_B64, "base64"),
      });
    }
    // models / production tasks / list cloned voices → empty
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });

  await page.goto("/workspace/twins/voice");
  await page.waitForLoadState("networkidle");

  // Open the production toolbox tab (VoiceSelector lives inside ScriptToAudio).
  await page
    .getByRole("button", { name: /课件配音工具箱|production/i })
    .click();

  // Open the picker (script-to-audio is the default sub-tab).
  await page.getByRole("combobox").first().click();
  await expect(page.getByText("知性女声").first()).toBeVisible();

  // Per-voice preview button → hits the intercepted /preview route.
  await page
    .getByRole("button", { name: /试听音色/ })
    .first()
    .click();
  await expect.poll(() => previewHit).toBe(true);

  // Selecting the voice fires onChange and closes the popover.
  await page.getByText("知性女声").first().click();
  await expect(page.getByRole("combobox").first()).toContainText("知性女声");
});
