/**
 * Per-type starter template tests (W30 — §2 "模板做实").
 *
 * New docs should open with a type-appropriate markdown structure so the
 * type choice has real meaning from creation. The first line must be
 * `# {title}` (consistent with W27 title-write-back: first H1 → stored title).
 *
 * TDD: red → green. These 4 tests mirror the brief's acceptance:
 *   1. each-type-nonempty  — all 6 types return nonempty + start with H1
 *   2. fills-title         — {title} placeholder is substituted into the H1
 *   3. distinct-per-type   — each type has a distinctive structural substring
 *   4. general-minimal     — general is just the H1, no full structure
 */

import { describe, expect, test } from "vitest";

import { DOC_TYPE_ORDER } from "@/core/writer/doc-type-config";
import {
  DOC_TYPE_TEMPLATES,
  getStarterTemplate,
} from "@/core/writer/doc-type-templates";
import type { DocType } from "@/core/writer/types";

describe("getStarterTemplate", () => {
  test("each type returns a nonempty string starting with an H1", () => {
    for (const docType of DOC_TYPE_ORDER) {
      const result = getStarterTemplate(docType, "测试标题");
      expect(
        result.length,
        `${docType} template must be nonempty`,
      ).toBeGreaterThan(0);
      // W27 consistency: first line must be a `# ` ATX heading so the
      // autosave title-write-back can derive the title from it.
      expect(
        result.startsWith("# "),
        `${docType} template must start with "# " (H1)`,
      ).toBe(true);
    }
  });

  test("fills the {title} placeholder into the H1 heading", () => {
    const result = getStarterTemplate("article", "我的文章");
    expect(result.startsWith("# 我的文章")).toBe(true);
    // No leftover {title} placeholder after substitution.
    expect(result).not.toContain("{title}");
  });

  test("templates are distinct with type-appropriate structure", () => {
    // Each type (except general) must contain a distinctive substring that
    // reflects its purpose. General is checked separately for minimality.
    const distinctive: Partial<Record<DocType, string>> = {
      article: "导语",
      case: "背景",
      script: "画面",
      podcast: "嘉宾",
      course: "教学目标",
    };

    for (const docType of DOC_TYPE_ORDER) {
      const needle = distinctive[docType];
      if (needle) {
        const result = getStarterTemplate(docType, "T");
        expect(
          result.includes(needle),
          `${docType} template must contain "${needle}"`,
        ).toBe(true);
      }
    }

    // All 6 templates must be distinct (no two identical).
    const templates = DOC_TYPE_ORDER.map((t) => DOC_TYPE_TEMPLATES[t]);
    const unique = new Set(templates);
    expect(unique.size).toBe(DOC_TYPE_ORDER.length);
  });

  test("general template is minimal — just the H1, no section structure", () => {
    const result = getStarterTemplate("general", "笔记");
    // Must start with the H1.
    expect(result.startsWith("# 笔记")).toBe(true);
    // Must NOT contain H2 section headings or structured prompts.
    expect(result).not.toContain("## ");
    // Body after the H1 line should be whitespace-only (minimal).
    const body = result.replace(/^#[^\n]*\n/, "");
    expect(body.trim().length).toBe(0);
  });

  test("falls back to general for an unknown doc type", () => {
    // Defensive: an unrecognized type should not crash — fall back to general.
    const result = getStarterTemplate("nonexistent" as DocType, "Fallback");
    expect(result.startsWith("# Fallback")).toBe(true);
  });
});
