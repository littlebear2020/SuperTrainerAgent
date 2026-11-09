/**
 * UploadTab validation tests (W21).
 *
 * Goal: make 本地上传 a first-class 配图 tab alongside AI 生图. These tests
 * pin the pure `isValidUploadFile` helper that gates the file-picker
 * (PNG/JPEG/GIF/WebP ≤10MB, no SVG) and its exported constants.
 *
 * The helper is re-exported from design-panel.tsx so it can be unit-tested in
 * Node without rendering the React component. The DesignPanel tab list itself
 * ([ai, upload, brand] with "ai" as the default) has NO exported pure seam —
 * the `tabs` array is inline in the component body — so it is not asserted
 * here. (2026-10-05 review: the header previously promised tab-list coverage
 * that did not exist in this file.)
 */

import { expect, test } from "vitest";

import {
  ACCEPTED_UPLOAD_MIME_TYPES,
  MAX_UPLOAD_BYTES,
  isValidUploadFile,
} from "@/components/workspace/writer/design-panel";

// ── isValidUploadFile ──────────────────────────────────────────────────────

test("isValidUploadFile accepts PNG, JPEG, GIF, and WebP under the size limit", () => {
  const validTypes = ["image/png", "image/jpeg", "image/gif", "image/webp"];
  for (const type of validTypes) {
    const file = new File(["x".repeat(100)], `ok.${type.split("/")[1]}`, {
      type,
    });
    expect(isValidUploadFile(file)).toBe(true);
  }
});

test("isValidUploadFile rejects SVG even though its MIME starts with image/", () => {
  const file = new File(["<svg></svg>"], "evil.svg", {
    type: "image/svg+xml",
  });
  expect(isValidUploadFile(file)).toBe(false);
});

test("isValidUploadFile rejects non-image MIME types", () => {
  const file = new File(["hello"], "doc.pdf", { type: "application/pdf" });
  expect(isValidUploadFile(file)).toBe(false);
});

test("isValidUploadFile rejects files over the 10MB size limit", () => {
  // Create a real PNG file then stub its size to 10MB+1 (avoid allocating 10MB).
  const file = new File(["x"], "big.png", { type: "image/png" });
  Object.defineProperty(file, "size", {
    value: MAX_UPLOAD_BYTES + 1,
    configurable: true,
  });
  expect(isValidUploadFile(file)).toBe(false);
});

test("isValidUploadFile accepts a file at exactly the 10MB size limit", () => {
  const file = new File(["x"], "max.png", { type: "image/png" });
  Object.defineProperty(file, "size", {
    value: MAX_UPLOAD_BYTES,
    configurable: true,
  });
  expect(isValidUploadFile(file)).toBe(true);
});

test("ACCEPTED_UPLOAD_MIME_TYPES is exactly the 4 backend-allowed types", () => {
  expect([...ACCEPTED_UPLOAD_MIME_TYPES].sort()).toEqual([
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
  ]);
});

test("MAX_UPLOAD_BYTES equals 10 MB", () => {
  expect(MAX_UPLOAD_BYTES).toBe(10 * 1024 * 1024);
});
