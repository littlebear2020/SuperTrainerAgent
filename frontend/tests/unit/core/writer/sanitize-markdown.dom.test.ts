/**
 * B6-T8: sanitizeMarkdownHtml tests.
 *
 * 6d addition: DOMPurify.sanitize() doesn't run correctly under happy-dom
 * (it needs a full browser DOM). So the sanitize-specific tests verify:
 * 1. DOMPurify is called with the correct security config (FORBID_TAGS, ADD_ATTR)
 * 2. Stash/restore integration — code blocks survive sanitize unmodified
 * 3. Non-code HTML is passed through to DOMPurify (not pre-filtered)
 * Actual XSS stripping is verified via manual browser testing (see E2E checklist #16/#17).
 */

import DOMPurify from "dompurify";
import { afterEach, expect, test, vi } from "vitest";

import {
  _restoreCodeBlocks,
  _stashCodeBlocks,
  sanitizeMarkdownHtml,
} from "@/core/writer/sanitize-markdown";

// Track DOMPurify.sanitize calls
let sanitizeCalls: { input: string; config: Record<string, unknown> }[] = [];

// ── Stash/restore: code blocks preserved literally ───────────────────────

test("stash: fenced code block preserved as placeholder", () => {
  const input = '```html\n<img onerror="alert(1)">\n```';
  const { stashed, placeholders } = _stashCodeBlocks(input);
  // The code block content should not appear in stashed text
  expect(stashed).not.toContain("onerror");
  // But it's preserved in the placeholders map
  expect(placeholders.size).toBe(1);
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
  expect(restored).toContain("onerror");
});

test("stash: inline code preserved as placeholder", () => {
  const input = "Use `<script>` tag carefully.";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(stashed).not.toContain("<script>");
  expect(placeholders.size).toBe(1);
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
  expect(restored).toContain("<script>");
});

test("stash: non-HTML markdown unaffected", () => {
  const input = "# Title\n\nSome **bold** text.";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(stashed).toBe(input);
  expect(placeholders.size).toBe(0);
});

test("stash: nested code fences (~~~ outer, ``` inner)", () => {
  const input = "~~~\n```\ncode\n```\n~~~";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(placeholders.size).toBe(1); // outer fence captured as one block
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
});

test("stash: unclosed fence (no closing ```)", () => {
  const input = "```\nsome code\n\nThis is still content.";
  // Unclosed fence: the regex won't match (needs closing ```), so
  // the fence content stays in the stashed text. This is correct --
  // DOMPurify will see the ``` but it's just text, not executable HTML.
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(stashed).toContain("some code");
  // Restore is a no-op (no placeholders)
  expect(placeholders.size).toBe(0);
});

test("stash: multiple code blocks get distinct placeholders", () => {
  const input = "```js\ncode1\n```\n\ntext\n\n```py\ncode2\n```";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(placeholders.size).toBe(2);
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
});

test("stash: mid-line fence marker is not a fence (B1, 2026-08-14)", () => {
  const input = "Use `code` then ```\nblock\n```";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  // Only the inline `code` is stashed. The mid-line ``` never opens a fence
  // (GFM: openers must be at line start), so "block" and both fence markers
  // stay literal and go through DOMPurify like ordinary content.
  expect(placeholders.size).toBe(1);
  expect(stashed).toContain("block");
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
});

// ── B4 (2026-08-13): variable-length fences (GFM 3+) + double-backtick inline ──
// 旧正则 /(```|~~~)([\s\S]*?)\1/g 只认 3 反引号/3 波浪号，4+ 围栏会吃错开闭。
// 注意：旧正则常能「意外 round-trip」（残余反引号拼回原文），故此处断言
// 占位符精确值——只有内容完整时才能绿。

test("stash: 4-backtick fence containing ``` code block stashes as one block", () => {
  const input = "````\nouter\n```\ninner\n```\nouter2\n````";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(placeholders.size).toBe(1);
  expect(placeholders.values().next().value).toBe(input);
  expect(stashed).not.toContain("inner");
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
});

test("stash: 5-backtick fence stashes whole block", () => {
  const input = "`````\ncode\n`````";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(placeholders.size).toBe(1);
  expect(placeholders.values().next().value).toBe(input);
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
});

test("stash: double-backtick inline code stashed as one placeholder", () => {
  const input = "Use ``<script>`` carefully.";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(stashed).not.toContain("<script>");
  expect(placeholders.size).toBe(1);
  expect(placeholders.values().next().value).toBe("``<script>``");
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
});

test("stash: unclosed 4-backtick fence stays unstashed", () => {
  const input = "````\nsome code\n\nStill content.";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(stashed).toContain("some code");
  expect(placeholders.size).toBe(0);
});

test("stash: fence at EOF without trailing newline", () => {
  const input = "````\ncode\n````";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(placeholders.size).toBe(1);
  expect(placeholders.values().next().value).toBe(input);
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
});

test("stash: open-3 close-4 fence stashes whole block (B2, GFM ≥ close)", () => {
  const input = "```\ncode\n````";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(placeholders.size).toBe(1);
  expect(placeholders.values().next().value).toBe(input);
  expect(stashed).not.toContain("code");
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
});

test("stash: close fence with trailing text does not close (B2)", () => {
  const input = "```\ncode\n``` js\n```";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(placeholders.size).toBe(1);
  expect(placeholders.values().next().value).toBe(input);
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
});

test("stash: inline code spanning a blank line is not stashed (B3)", () => {
  const input = "`a\n\nb`";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(placeholders.size).toBe(0);
  expect(stashed).toBe(input);
});

test("stash: tilde fences including 4-tilde variable length", () => {
  for (const fence of ["~~~", "~~~~"]) {
    const input = `${fence}\ncode\n${fence}`;
    const { stashed, placeholders } = _stashCodeBlocks(input);
    expect(placeholders.size).toBe(1);
    expect(placeholders.values().next().value).toBe(input);
    const restored = _restoreCodeBlocks(stashed, placeholders);
    expect(restored).toBe(input);
  }
});

test("stash: fence with info string (```python)", () => {
  const input = "```python\nprint('x')\n```";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(placeholders.size).toBe(1);
  expect(placeholders.values().next().value).toBe(input);
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input);
});

test("stash: equal-length backtick run in content closes; shorter stays in content", () => {
  // shorter run (3 < 4) must stay inside the 4-backtick fence's content
  const preserved = "````\nbefore\n```\nafter\n````";
  const r1 = _stashCodeBlocks(preserved);
  expect(r1.placeholders.size).toBe(1);
  expect(r1.placeholders.values().next().value).toBe(preserved);
  expect(_restoreCodeBlocks(r1.stashed, r1.placeholders)).toBe(preserved);

  // equal-length run (4) terminates the fence
  const terminated = "````\nbefore\n````\nafter";
  const r2 = _stashCodeBlocks(terminated);
  expect(r2.placeholders.size).toBe(1);
  expect(r2.placeholders.values().next().value).toBe("````\nbefore\n````");
  expect(_restoreCodeBlocks(r2.stashed, r2.placeholders)).toBe(terminated);
});

test("stash: user content resembling a placeholder token is preserved as-is", () => {
  // A2 (2026-08-14): placeholder keys carry a random per-call nonce
  // (ZSFC-<nonce>-<counter>), so user content can never predictably collide
  // with a real key. Content that merely LOOKS like a token must survive
  // the round-trip unchanged — only the real stashed key gets restored.
  const input = "Normal text with ZSFC-abc12345-0 in it\n\n```\ncode\n```";
  const { stashed, placeholders } = _stashCodeBlocks(input);
  expect(stashed).toContain("ZSFC-abc12345-0"); // user's text stays put
  expect(placeholders.size).toBe(1); // only the code block is stashed
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).toBe(input); // perfect round-trip
});

// ── 6d: DOMPurify sanitize config verification (security-critical) ──────
// DOMPurify.sanitize() needs a full browser DOM which happy-dom doesn't
// provide. These tests mock DOMPurify.sanitize to verify OUR configuration
// is correct: the right tags are forbidden, the right attributes are allowed,
// and code blocks are stashed/restored around the sanitize call.

afterEach(() => {
  sanitizeCalls = [];
  vi.restoreAllMocks();
});

function mockSanitizePassthrough() {
  // Mock DOMPurify.sanitize to return input unchanged (so we can inspect
  // what was passed and verify stash/restore works around it)
  vi.spyOn(DOMPurify, "sanitize").mockImplementation(((
    input: string,
    config?: Record<string, unknown>,
  ) => {
    sanitizeCalls.push({ input, config: config ?? {} });
    return input;
  }) as typeof DOMPurify.sanitize);
}

function mockSanitizeNulDrop() {
  // A2 (2026-08-14): simulate the real browser/parse5 parser behavior that
  // happy-dom does not implement — NUL characters are discarded during HTML
  // tree construction. A \x00-based placeholder therefore cannot survive the
  // DOMPurify round-trip; this is the P0 data-loss bug being guarded.
  vi.spyOn(DOMPurify, "sanitize").mockImplementation(((
    input: string,
    config?: Record<string, unknown>,
  ) => {
    sanitizeCalls.push({ input, config: config ?? {} });
    return input.replace(/\x00/g, "");
  }) as typeof DOMPurify.sanitize);
}

test("sanitize: DOMPurify called with FORBID_TAGS including script/iframe", () => {
  mockSanitizePassthrough();
  sanitizeMarkdownHtml("some content");
  expect(sanitizeCalls).toHaveLength(1);
  expect(sanitizeCalls[0]!.config.FORBID_TAGS).toEqual([
    "script",
    "iframe",
    "object",
    "embed",
  ]);
});

test("sanitize: DOMPurify called with ADD_ATTR including width/height", () => {
  mockSanitizePassthrough();
  sanitizeMarkdownHtml("some content");
  expect(sanitizeCalls[0]!.config.ADD_ATTR).toEqual([
    "width",
    "height",
    "style",
  ]);
});

test("sanitize: code blocks stashed before DOMPurify call", () => {
  mockSanitizePassthrough();
  const codeContent = "<script>alert(1)</script>";
  sanitizeMarkdownHtml(`\`\`\`html\n${codeContent}\n\`\`\``);
  // The stashed input should NOT contain the raw <script> — it's replaced
  // with a random ASCII token placeholder (A2, 2026-08-14)
  expect(sanitizeCalls[0]!.input).not.toContain(codeContent);
  expect(sanitizeCalls[0]!.input).toMatch(/ZSFC-[a-z0-9]+-\d+/);
});

test("sanitize: code blocks restored after DOMPurify call", () => {
  mockSanitizePassthrough();
  const input = "```html\n<img onerror=alert(1)>\n```";
  const result = sanitizeMarkdownHtml(input);
  // The final result should contain the original code block intact
  expect(result).toContain("<img onerror=alert(1)>");
  expect(result).toContain("```");
});

test("sanitize: mid-line ``` does not bypass DOMPurify for following HTML (B1)", () => {
  mockSanitizePassthrough();
  const input = "Use `code` then ```\n<img onerror=alert(1)>\n```";
  sanitizeMarkdownHtml(input);
  // The mid-line ``` is not a fence opener, so the <img> is ordinary
  // content and must reach DOMPurify (which strips it in a real browser).
  // Under the old stash-it-anyway behavior this img never reached DOMPurify.
  expect(sanitizeCalls[0]!.input).toContain("<img onerror=alert(1)>");
});

test("sanitize: code survives a NUL-discarding parser (A2 P0 regression)", () => {
  // Real browsers discard NUL bytes while parsing HTML, so \x00-based
  // placeholders never come back from DOMPurify and code-block content is
  // silently lost (then written back to the server on next save). Placeholder
  // tokens must be NUL-free ASCII to survive the round-trip.
  mockSanitizeNulDrop();
  const input =
    '```html\n<img onerror="alert(1)">\n```\n\nUse `<script>` carefully.';
  const result = sanitizeMarkdownHtml(input);
  // Code content preserved literally (this is teaching text, not XSS)
  expect(result).toContain('<img onerror="alert(1)">');
  expect(result).toContain("`<script>`");
  // No placeholder residue of any kind
  expect(result).not.toMatch(/ZSFC|ZSIN/);
});

test("sanitize: inline code stashed before DOMPurify call", () => {
  mockSanitizePassthrough();
  sanitizeMarkdownHtml("Use `<script>` tag carefully");
  // Inline code with <script> should be stashed, not passed raw to DOMPurify
  expect(sanitizeCalls[0]!.input).not.toContain("<script>");
  expect(sanitizeCalls[0]!.input).toMatch(/ZSIN-[a-z0-9]+-\d+/);
});

test("sanitize: non-code HTML passed through to DOMPurify", () => {
  mockSanitizePassthrough();
  sanitizeMarkdownHtml('<img src="photo.jpg" width="200">');
  // The img tag should be passed to DOMPurify (not stashed)
  expect(sanitizeCalls[0]!.input).toContain(
    '<img src="photo.jpg" width="200">',
  );
});

// ── P1（2026-08-18）：嵌套 stash 反向还原 ─────────────────────────────────

test("嵌套 stash：行内对跨越围栏时还原完整且无 token 残留（P1）", () => {
  // 现实触发形态：撇号误打成反引号 + 后文恰有一对行内代码——行内正则会把
  // 含 ZSFC key 的跨段文本收进 ZSIN 的 value。正序还原丢代码内容 + 字面
  // "ZSFC-xxx-0" 残留（并被 onUpdate 持久化）；反向还原完整。
  const md = "It`s a demo:\n```js\nsecret code\n```\nEnd `here`.";
  const { stashed, placeholders } = _stashCodeBlocks(md);
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).not.toMatch(/ZSFC|ZSIN/);
  expect(restored).toContain("secret code");
  expect(restored).toContain("It`s a demo:");
  expect(restored).toContain("`here`");
});

test("≥11 个同类占位符无前缀撕裂（P1 二审补充）", () => {
  // counter≥10 时 "ZSIN-x-1" 是 "ZSIN-x-10" 的前缀——正序还原先还原 -1 会把
  // -10 撕裂成 "-0"；反向（counter 大者先还原）天然规避。与嵌套 bug 同源，
  // 逆序还原一并修复（评估二审补充发现）。
  const md = Array.from({ length: 12 }, (_, i) => `前缀${i} \`code${i}\``).join(
    "\n",
  );
  const { stashed, placeholders } = _stashCodeBlocks(md);
  expect(placeholders.size).toBe(12);
  const restored = _restoreCodeBlocks(stashed, placeholders);
  expect(restored).not.toMatch(/ZSFC|ZSIN/);
  expect(restored).toContain("`code10`");
  expect(restored).toContain("`code11`");
});

// ── P2-1/P2-2（2026-08-18）：空行判定正则化 + randomUUID 三级降级 ─────────

test("跨空白空行的伪行内代码不 stash（P2-1）", () => {
  // 首个反引号无近配对时，行内正则会跨 "\n \n"（CommonMark blank line）匹配；
  // 修前只拦 "\n\n"/"\r\n\r\n" 两字面量，其间 HTML 绕过 DOMPurify。
  const md = "x`y\n \n<img onerror=z>`w`";
  const { stashed, placeholders } = _stashCodeBlocks(md);
  expect(placeholders.size).toBe(0);
  expect(stashed).toBe(md);
});

test("混合行尾 \\n\\r\\n 不 stash（P2-1）", () => {
  const md = "x`y\n\r\n<img src=q>`w`";
  const { placeholders } = _stashCodeBlocks(md);
  expect(placeholders.size).toBe(0);
});

test("crypto.randomUUID 不可用时回退 getRandomValues（P2-2）", () => {
  const realCrypto = globalThis.crypto;
  vi.stubGlobal("crypto", {
    getRandomValues: (a: Uint8Array) => realCrypto.getRandomValues(a),
  });
  try {
    const { stashed, placeholders } = _stashCodeBlocks("`code`");
    expect(placeholders.size).toBe(1);
    expect(stashed).toMatch(/^ZSIN-[0-9a-f]{8}-0$/);
  } finally {
    vi.unstubAllGlobals();
  }
});

test("Web Crypto 全缺时仍可用（弱 nonce + warning）（P2-2）", () => {
  vi.stubGlobal("crypto", undefined);
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  try {
    const { stashed, placeholders } = _stashCodeBlocks("`code`");
    expect(placeholders.size).toBe(1);
    expect(stashed).toMatch(/^ZSIN-[0-9a-f]{8}-0$/);
    expect(warn).toHaveBeenCalled();
  } finally {
    vi.unstubAllGlobals();
    warn.mockRestore();
  }
});
