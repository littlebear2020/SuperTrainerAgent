import { expect, test } from "@playwright/test";

import { mockWriterAPI, type MockWriterDoc } from "./utils/mock-writer-api";

const DOC_ID = "doc-e2e-editor";

const DOC: MockWriterDoc = {
  id: DOC_ID,
  title: "E2E 课程文档",
  content_json: "# E2E 课程\n\n初始内容",
  doc_type: "course",
  version: 3,
};

test("writer list opens editor, autosaves edits, and exposes versions", async ({
  page,
}) => {
  const api = mockWriterAPI(page, [DOC]);
  api.setVersions(DOC_ID, [
    {
      id: "version-3",
      doc_id: DOC_ID,
      version_number: 3,
      title: "E2E 课程文档",
      snapshot_reason: "auto",
      created_at: "2026-10-17T01:00:00Z",
    },
  ]);

  await page.goto("/workspace/writer");
  await expect(page.getByText("E2E 课程文档")).toBeVisible({
    timeout: 10_000,
  });
  await page.getByRole("button", { name: /E2E 课程文档/ }).click();

  await expect(page).toHaveURL(new RegExp(`/workspace/writer/${DOC_ID}$`));
  const editor = page.locator(".ProseMirror");
  await expect(editor).toBeVisible({ timeout: 10_000 });
  await expect(editor.getByText("E2E 课程", { exact: true })).toBeVisible();
  await expect(editor.getByText("初始内容")).toBeVisible();

  await editor.getByText("初始内容").click();
  await page.keyboard.press("End");
  await page.keyboard.type("，新增内容");

  await expect
    .poll(() => api.updateRequests.length, { timeout: 8_000 })
    .toBeGreaterThan(0);
  const update = api.updateRequests[0];
  expect(update).toMatchObject({
    method: "PUT",
    url: `/api/writer/docs/${DOC_ID}`,
  });
  expect(update?.body).toMatchObject({
    content_json: expect.stringContaining("初始内容，新增内容"),
    force_snapshot: false,
    base_version: 3,
  });

  await page.getByTitle("版本历史").click();
  await expect(page.getByRole("heading", { name: "版本历史" })).toBeVisible();
  await expect(page.getByText("自动保存")).toBeVisible();
  await expect(page.getByText("v3")).toBeVisible();
});
