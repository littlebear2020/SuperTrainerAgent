/**
 * Brand template helpers tests (W20).
 *
 * The brand design tab converges to templated generation: preset HTML
 * templates + light AI fill (JSON vars) → frontend renders locally (trusted
 * template + escaped vars → NO sanitizeHtml). These tests pin the two pure
 * helpers: `parseBrandVars` (tolerant JSON extraction from streamed text) and
 * `renderBrandTemplate` (fills placeholders, escapes text vars, picks the
 * right template per type).
 *
 * Spec: docs/superpowers/plans/2026-07-10-writer-focus-refactor-plan.md §8.4.
 */

import { expect, test } from "vitest";

import {
  type BrandVars,
  parseBrandVars,
  renderBrandTemplate,
} from "@/components/workspace/writer/brand-templates";

// ── parseBrandVars ──────────────────────────────────────────────────────────

test("parseBrandVars_valid_json: parses a clean JSON object into BrandVars", () => {
  const raw = JSON.stringify({
    title: "T",
    subtitle: "S",
    points: ["a", "b"],
    footer: "F",
    colors: { primary: "#111" },
  });

  const result = parseBrandVars(raw);
  expect(result).not.toBeNull();
  expect(result!.title).toBe("T");
  expect(result!.subtitle).toBe("S");
  expect(result!.points).toEqual(["a", "b"]);
  expect(result!.footer).toBe("F");
  expect(result!.colors.primary).toBe("#111");
});

test("parseBrandVars_tolerant: extracts JSON from fences and surrounding text", () => {
  const raw = [
    "好的，以下是设计文案：",
    "```json",
    JSON.stringify({
      title: "封面标题",
      subtitle: "副标题",
      points: ["要点一", "要点二"],
      footer: "页脚",
      colors: { primary: "#1a1a2e", accent: "#e94560" },
    }),
    "```",
    "希望对你有帮助！",
  ].join("\n");

  const result = parseBrandVars(raw);
  expect(result).not.toBeNull();
  expect(result!.title).toBe("封面标题");
  expect(result!.subtitle).toBe("副标题");
  expect(result!.points).toEqual(["要点一", "要点二"]);
  expect(result!.footer).toBe("页脚");
  expect(result!.colors.primary).toBe("#1a1a2e");
  expect(result!.colors.accent).toBe("#e94560");
});

test("parseBrandVars_null_on_garbage: returns null for non-JSON text", () => {
  expect(parseBrandVars("This is not JSON at all.")).toBeNull();
  expect(parseBrandVars("")).toBeNull();
  expect(parseBrandVars("```\nnot json\n```")).toBeNull();
  // JSON array is not a valid BrandVars object
  expect(parseBrandVars('["a","b"]')).toBeNull();
  // JSON object with no useful fields
  expect(parseBrandVars('{"foo":"bar"}')).toBeNull();
});

// ── renderBrandTemplate ─────────────────────────────────────────────────────

const FULL_VARS: BrandVars = {
  title: "My Title",
  subtitle: "My Subtitle",
  points: ["Point A", "Point B"],
  footer: "Footer Text",
  colors: {
    primary: "#ff0000",
    secondary: "#00ff00",
    accent: "#0000ff",
    bg: "#ffffff",
  },
};

test("renderBrandTemplate_fills_placeholders: all {{}} replaced, content + colors present", () => {
  const html = renderBrandTemplate("cover", FULL_VARS);

  // Text content is present
  expect(html).toContain("My Title");
  expect(html).toContain("My Subtitle");
  expect(html).toContain("Point A");
  expect(html).toContain("Point B");
  expect(html).toContain("Footer Text");

  // Color values are present
  expect(html).toContain("#ff0000");
  expect(html).toContain("#00ff00");
  expect(html).toContain("#0000ff");
  expect(html).toContain("#ffffff");

  // No unresolved placeholders remain
  expect(html).not.toMatch(/\{\{/);
});

test("renderBrandTemplate_escapes_text_vars: <script> in title is escaped, not live", () => {
  const html = renderBrandTemplate("cover", {
    title: "<script>alert(1)</script>",
    subtitle: "<img src=x onerror=alert(1)>",
    points: ["<b>bold</b>"],
    footer: "",
    colors: {},
  });

  // Script tag is escaped
  expect(html).toContain("&lt;script&gt;");
  expect(html).not.toContain("<script>alert");

  // onerror attribute is escaped (the < > around img are escaped)
  expect(html).toContain("&lt;img");
  expect(html).not.toMatch(/<img src=x onerror/);

  // Points are escaped too
  expect(html).toContain("&lt;b&gt;bold&lt;/b&gt;");
  expect(html).not.toContain("<b>bold</b>");
});

test("renderBrandTemplate_each_type: cover + social + chart each render", () => {
  const coverHtml = renderBrandTemplate("cover", FULL_VARS);
  const socialHtml = renderBrandTemplate("social", FULL_VARS);
  const chartHtml = renderBrandTemplate("chart", FULL_VARS);

  // All non-empty and contain the title
  expect(coverHtml.length).toBeGreaterThan(0);
  expect(socialHtml.length).toBeGreaterThan(0);
  expect(chartHtml.length).toBeGreaterThan(0);

  expect(coverHtml).toContain("My Title");
  expect(socialHtml).toContain("My Title");
  expect(chartHtml).toContain("My Title");

  // All points rendered
  expect(coverHtml).toContain("Point A");
  expect(socialHtml).toContain("Point A");
  expect(chartHtml).toContain("Point A");

  // No unresolved placeholders in any
  expect(coverHtml).not.toMatch(/\{\{/);
  expect(socialHtml).not.toMatch(/\{\{/);
  expect(chartHtml).not.toMatch(/\{\{/);

  // Templates are distinct (different structure)
  expect(coverHtml).not.toEqual(socialHtml);
  expect(coverHtml).not.toEqual(chartHtml);
  expect(socialHtml).not.toEqual(chartHtml);
});
