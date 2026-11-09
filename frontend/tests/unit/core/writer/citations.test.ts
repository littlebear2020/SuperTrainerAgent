/**
 * parseCitations pure-helper tests (Task W22 — §8.6 citation list).
 *
 * The co-writer appends a `[[CITATIONS:<json>]]` marker at the END of its
 * response when web-search sources were used. `parseCitations` detects the
 * trailing marker, parses the JSON array `{title, url, org}[]`, and returns
 * the citations plus the body with the marker stripped.
 *
 * Tolerant contract: malformed JSON → null citations, no crash. The marker is
 * stripped from the body even when malformed (so the broken token never leaks
 * into the visible text).
 */

import { describe, expect, it, test } from "vitest";

import { parseCitations, sanitizeCitationUrl } from "@/core/writer/citations";

// ── 1. Valid marker → citations parsed, body stripped ─────────────────────

test("parseCitations_valid detects marker, parses array, strips from body", () => {
  const input =
    '正文内容\n\n[[CITATIONS:[{"title":"T","url":"https://x.com","org":"X"}]]]';
  const result = parseCitations(input);

  expect(result.citations).not.toBeNull();
  expect(result.citations).toHaveLength(1);
  const c = result.citations![0]!;
  expect(c.title).toBe("T");
  expect(c.url).toBe("https://x.com");
  expect(c.org).toBe("X");
  // Body has marker + preceding whitespace stripped
  expect(result.body).toBe("正文内容");
  expect(result.body).not.toContain("[[CITATIONS:");
});

// ── 2. No marker → null citations, body unchanged ──────────────────────────

test("parseCitations_null_when_absent returns null citations and unchanged body", () => {
  const input = "这是一段没有引用标记的正文。";
  const result = parseCitations(input);

  expect(result.citations).toBeNull();
  expect(result.body).toBe(input);
});

// ── 3. Malformed JSON → null citations, no crash, marker stripped ──────────

test("parseCitations_tolerant_malformed returns null citations, marker stripped from body", () => {
  // The JSON inside the marker is not valid JSON.
  const input = "正文内容\n\n[[CITATIONS:[not json]]]";
  const result = parseCitations(input);

  // No crash; citations null.
  expect(result.citations).toBeNull();
  // Marker is stripped from body so the broken token doesn't leak into the
  // visible text (design choice — documented here).
  expect(result.body).toBe("正文内容");
  expect(result.body).not.toContain("[[CITATIONS:");
});

// ── 4. Multiple citations parsed ───────────────────────────────────────────

test("parseCitations_multiple parses 2+ citations and strips marker", () => {
  const input =
    '正文内容\n\n[[CITATIONS:[{"title":"来源一","url":"https://a.com","org":"A机构"},{"title":"来源二","url":"https://b.com","org":"B网站"}]]]';
  const result = parseCitations(input);

  expect(result.citations).not.toBeNull();
  expect(result.citations).toHaveLength(2);
  expect(result.citations![0]!.title).toBe("来源一");
  expect(result.citations![0]!.org).toBe("A机构");
  expect(result.citations![1]!.title).toBe("来源二");
  expect(result.citations![1]!.url).toBe("https://b.com");
  expect(result.body).toBe("正文内容");
});

// ── Edge cases ─────────────────────────────────────────────────────────────

test("parseCitations_empty_array returns null citations (no list to show)", () => {
  const input = "正文内容\n\n[[CITATIONS:[]]]";
  const result = parseCitations(input);

  // Empty array → null (no citation list rendered).
  expect(result.citations).toBeNull();
  expect(result.body).toBe("正文内容");
});

test("parseCitations_items_missing_fields returns only valid citations", () => {
  // One item has all fields, one is missing org, one is not an object.
  const input =
    '正文\n\n[[CITATIONS:[{"title":"OK","url":"https://ok.com","org":"OK"},{"title":"NoOrg","url":"https://no.org"},"not-an-object"]]]';
  const result = parseCitations(input);

  // Only the well-formed citation is returned; malformed items are skipped.
  expect(result.citations).not.toBeNull();
  expect(result.citations).toHaveLength(1);
  expect(result.citations![0]!.title).toBe("OK");
  expect(result.body).toBe("正文");
});

test("parseCitations_marker_embeded_mid_text is ignored (only trailing marker)", () => {
  // A marker in the middle of the text is NOT a trailing citation marker —
  // it could be body content. Only a marker at the END is parsed.
  const input =
    '[[CITATIONS:[{"title":"T","url":"https://x.com","org":"X"}]]]\n\n这是正文';
  const result = parseCitations(input);

  expect(result.citations).toBeNull();
  expect(result.body).toBe(input);
});

test("parseCitations_type guard: returns Citation[] shape", () => {
  const input =
    '正文\n\n[[CITATIONS:[{"title":"T","url":"https://x.com","org":"X"}]]]';
  const result = parseCitations(input);

  // Each citation has exactly {title, url, org} — all strings.
  if (result.citations) {
    for (const c of result.citations) {
      expect(typeof c.title).toBe("string");
      expect(typeof c.url).toBe("string");
      expect(typeof c.org).toBe("string");
    }
  }
});

test("P1-6: 正文中段出现标记 + 末尾真标记 → 只剥末尾标记，正文完整保留", () => {
  // 模型在正文里复述了引用规则（提及 [[CITATIONS:<json>]]），末尾才是真标记。
  const raw = [
    "本文遵循的规则是：输出结束后追加 [[CITATIONS:<json>]] 标记。",
    "这是正文第二段，必须保留。",
    '[[CITATIONS:[{"title":"来源","url":"https://a.com","org":"A"}]]]',
  ].join("\n\n");
  const result = parseCitations(raw);
  expect(result.citations).toHaveLength(1);
  expect(result.citations?.[0]?.title).toBe("来源");
  // 正文（含复述规则那行）必须完整保留——旧贪婪正则会从首个标记处吞掉之后全部正文
  expect(result.body).toContain("本文遵循的规则是");
  expect(result.body).toContain("这是正文第二段，必须保留。");
});

// ── P1-13: citation URL scheme allowlist ──────────────────────────────────

describe("sanitizeCitationUrl (P1-13)", () => {
  it("allows http/https and protocol-relative URLs", () => {
    expect(sanitizeCitationUrl("https://example.com/a")).toBe(
      "https://example.com/a",
    );
    expect(sanitizeCitationUrl("http://example.com")).toBe(
      "http://example.com",
    );
    expect(sanitizeCitationUrl("//example.com/x")).toBe("//example.com/x");
  });

  it("rejects javascript:/data:/vbscript: and junk", () => {
    expect(sanitizeCitationUrl("javascript:alert(1)")).toBeNull();
    expect(sanitizeCitationUrl("JavaScript:alert(1)")).toBeNull();
    expect(sanitizeCitationUrl("data:text/html,<script>")).toBeNull();
    expect(sanitizeCitationUrl("vbscript:x")).toBeNull();
    expect(sanitizeCitationUrl("")).toBeNull();
    expect(sanitizeCitationUrl("not a url")).toBeNull();
  });
});
