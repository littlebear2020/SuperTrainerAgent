/**
 * P2-13 (wiring): the artifact HTML preview must actually feed the
 * base-href-injected content into the iframe, not just have a helper available.
 *
 * The preview builds a `blob:` object URL from the artifact content. A blob
 * URL has an opaque path, so relative `img`/`css`/`js` URLs inside it resolve
 * against nothing — in real Chromium the request is never even issued. This
 * test captures the Blob handed to URL.createObjectURL and asserts the
 * `<base>` pointing at the artifact's directory made it into the document.
 *
 * Repo has no @testing-library/react: uses the createRoot + act + happy-dom
 * harness that the other component tests here use.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// streamdown imports katex CSS at module level and vitest has no CSS
// transform. This test only exercises the html branch, so stub it out (same
// treatment as the other component tests in this repo).
vi.mock("streamdown", () => ({
  Streamdown: () => null,
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: {} }),
}));

import { ArtifactFilePreview } from "@/components/workspace/artifacts/artifact-file-detail";

const ARTIFACT_URL =
  "http://backend/api/threads/t1/artifacts/mnt/user-data/outputs/report.html";
const ARTIFACT_DIR =
  "http://backend/api/threads/t1/artifacts/mnt/user-data/outputs/";

let container: HTMLDivElement;
let root: Root;
let blobs: Blob[];

beforeEach(() => {
  blobs = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  vi.spyOn(URL, "createObjectURL").mockImplementation(
    (blob: Blob | MediaSource) => {
      blobs.push(blob as Blob);
      return "blob:mock";
    },
  );
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
  vi.restoreAllMocks();
});

async function renderPreview(content: string, url?: string) {
  await act(async () => {
    root = createRoot(container);
    root.render(
      <ArtifactFilePreview content={content} language="html" url={url} />,
    );
  });
  expect(blobs.length).toBeGreaterThan(0);
  return await blobs[blobs.length - 1]!.text();
}

test("injects a base href pointing at the artifact directory into the previewed HTML", async () => {
  const html = await renderPreview(
    "<!doctype html><html><head></head><body><img src='assets/logo.png'></body></html>",
    ARTIFACT_URL,
  );

  expect(html).toContain(`<base href="${ARTIFACT_DIR}">`);
});

test("previewed HTML without a url is left untouched", async () => {
  const source = "<html><head></head><body>x</body></html>";

  expect(await renderPreview(source, undefined)).toBe(source);
});

test("does not inject a second base tag", async () => {
  const source =
    '<html><head><base href="https://example.com/"></head><body>x</body></html>';

  const html = await renderPreview(source, ARTIFACT_URL);

  expect(html.match(/<base\s/gi)).toHaveLength(1);
});
