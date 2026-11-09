/**
 * Doc type config tests.
 *
 * History (W24/D-3): the 自动标注 feature was broken×2 (data-rough-annotation
 * vs data-rough-notation render mismatch + 头尾30字校验 false-alarms) and was
 * default-hidden via `allowAnnotation: false` for all doc types. D-3
 * (2026-07-27) removed the feature entirely — SOUL teaching, AutoMark button/
 * handler, rough-annotation renderer, and the allowAnnotation field — so the
 * only invariant left to pin is config/order alignment below.
 */

import { describe, expect, test } from "vitest";

import {
  DOC_TYPE_CONFIGS,
  DOC_TYPE_ORDER,
} from "@/core/writer/doc-type-config";

describe("DOC_TYPE_CONFIGS", () => {
  test("every DOC_TYPE_ORDER entry has a matching config", () => {
    for (const docType of DOC_TYPE_ORDER) {
      expect(DOC_TYPE_CONFIGS[docType]).toBeDefined();
    }
  });
});
