/**
 * P2-13: HTML artifact preview injects a <base href> so relative asset paths
 * resolve against the artifact's own directory.
 *
 * The preview renders agent-produced HTML inside an iframe fed by a
 * `blob:` object URL. A blob URL has an *opaque path*, so a relative
 * `src="assets/logo.png"` has nothing to resolve against — measured in real
 * Chromium, the browser does not even issue a request (the img `onerror`
 * fires and the server never sees a hit). The preview is therefore a bare
 * skeleton with broken images.
 *
 * Injecting `<base href="<artifact directory>/">` gives those relative URLs an
 * absolute base pointing back at the artifact endpoint.
 */
import { describe, expect, it } from "vitest";

import { appendHtmlPreviewBaseHref } from "@/core/artifacts/preview";

const ARTIFACT_URL =
  "http://backend/api/threads/t1/artifacts/mnt/user-data/outputs/report.html";
const ARTIFACT_DIR =
  "http://backend/api/threads/t1/artifacts/mnt/user-data/outputs/";

describe("appendHtmlPreviewBaseHref", () => {
  it("points the base at the artifact's directory, not the file itself", () => {
    const result = appendHtmlPreviewBaseHref(
      "<!doctype html><html><head></head><body><img src='assets/logo.png'></body></html>",
      ARTIFACT_URL,
    );

    expect(result).toContain(`<base href="${ARTIFACT_DIR}">`);
    // The file itself must never become the base — that would resolve
    // "assets/logo.png" to ".../outputs/assets/logo.png" only by accident of
    // trailing-slash handling, and sibling files would break.
    expect(result).not.toContain(`<base href="${ARTIFACT_URL}">`);
  });

  it("injects into <head> when present, preserving existing head content", () => {
    const result = appendHtmlPreviewBaseHref(
      '<!doctype html><html><head><meta charset="utf-8"></head><body>x</body></html>',
      ARTIFACT_URL,
    );

    expect(result).toContain(`<head><base href="${ARTIFACT_DIR}">`);
    expect(result).toContain('<meta charset="utf-8">');
  });

  it("keeps attributes on the head tag", () => {
    const result = appendHtmlPreviewBaseHref(
      '<html><head lang="zh"><title>t</title></head><body>x</body></html>',
      ARTIFACT_URL,
    );

    expect(result).toContain(`<head lang="zh"><base href="${ARTIFACT_DIR}">`);
  });

  it("still injects when the document has no <head>", () => {
    const result = appendHtmlPreviewBaseHref(
      "<div>fragment only</div>",
      ARTIFACT_URL,
    );

    expect(result).toBe(
      `<base href="${ARTIFACT_DIR}"><div>fragment only</div>`,
    );
  });

  it("does not add a second <base> when the document already has one", () => {
    const html =
      '<html><head><base href="https://example.com/"></head><body>x</body></html>';

    const result = appendHtmlPreviewBaseHref(html, ARTIFACT_URL);

    expect(result).toBe(html);
    expect(result.match(/<base\s/gi)).toHaveLength(1);
  });

  it("strips the query string so ?download=true is not inherited by assets", () => {
    const result = appendHtmlPreviewBaseHref(
      "<html><head></head><body>x</body></html>",
      `${ARTIFACT_URL}?download=true`,
    );

    expect(result).toContain(`<base href="${ARTIFACT_DIR}">`);
    expect(result).not.toContain("download=true");
  });

  it("resolves a relative artifact URL against the current page", () => {
    // Production runs behind nginx with NEXT_PUBLIC_BACKEND_BASE_URL unset, so
    // urlOfArtifact returns a root-relative URL. A relative <base href> inside
    // a blob: document would be useless, so it must be absolutised.
    const result = appendHtmlPreviewBaseHref(
      "<html><head></head><body>x</body></html>",
      "/api/threads/t1/artifacts/mnt/user-data/outputs/report.html",
      "http://localhost:2026/workspace/chats/t1",
    );

    expect(result).toContain(
      '<base href="http://localhost:2026/api/threads/t1/artifacts/mnt/user-data/outputs/">',
    );
  });

  it("cannot be broken out of the href attribute by a quote in the filename", () => {
    const result = appendHtmlPreviewBaseHref(
      "<html><head></head><body>x</body></html>",
      '/api/threads/t1/artifacts/mnt/outputs/a"onload="alert(1)/report.html',
      "http://localhost:2026/",
    );

    // URL normalisation already percent-encodes the quote; assert the outcome
    // (no attribute breakout) rather than which layer did the escaping.
    expect(result).not.toContain('"onload="alert(1)');
    expect(result).toContain("a%22onload=%22alert(1)/");
    expect(/<base href="[^"]*">/.exec(result)).not.toBeNull();
  });

  it("escapes an ampersand, which URL normalisation leaves intact", () => {
    const result = appendHtmlPreviewBaseHref(
      "<html><head></head><body>x</body></html>",
      "/api/threads/t1/artifacts/mnt/outputs/a&b/report.html",
      "http://localhost:2026/",
    );

    expect(result).toContain(
      '<base href="http://localhost:2026/api/threads/t1/artifacts/mnt/outputs/a&amp;b/">',
    );
  });

  it("returns the content untouched when there is no URL", () => {
    const html = "<html><head></head><body>x</body></html>";

    expect(appendHtmlPreviewBaseHref(html, undefined)).toBe(html);
  });
});
