/**
 * B6-T8: Sanitize markdown HTML content with DOMPurify.
 *
 * The TipTap editor uses `html: true` in its Markdown config so that
 * `<img width=... height=...>` survives the markdown round-trip (critical
 * for image sizing). This means raw HTML in the markdown is passed to the
 * editor. DOMPurify strips dangerous tags/attributes (script, on* events)
 * while preserving img width/height/style.
 *
 * Code fences and inline code are stashed as placeholders before
 * sanitization so that HTML inside code examples (e.g. `<script>` as
 * teaching text) is preserved literally, not stripped by DOMPurify.
 *
 * Placeholder keys are random ASCII tokens (`ZSFC-<nonce>-<n>` /
 * `ZSIN-<nonce>-<n>`) by design (2026-08-14, P0 fix): they must survive the
 * HTML parse round-trip byte-for-byte (NUL sentinels get discarded by the
 * parser and silently destroyed code content), and the per-call random nonce
 * makes collisions with user content unpredictable.
 */

import DOMPurify from "dompurify";

/**
 * Get a DOMPurify instance with sanitize method.
 *
 * In browser: the default export already has .sanitize (auto-detected window).
 * In happy-dom (vitest): the default export is a factory that needs to be
 * called with window to produce an instance.
 */
function getPurify(): {
  sanitize: (dirty: string, config?: Record<string, unknown>) => string;
} {
  if (typeof DOMPurify.sanitize === "function") {
    return DOMPurify;
  }
  // Factory mode (test environment): call with window
  const instance = (
    DOMPurify as unknown as (w: Window) => {
      sanitize: (d: string, c?: Record<string, unknown>) => string;
    }
  )(typeof window !== "undefined" ? window : ({} as Window));
  return instance;
}

// ── Fence stash: stepwise scan (B4, 2026-08-13) ────────────────────────────
// GFM allows 3+ backticks/tildes with a close of at least the opening
// length; a single regex with backreference mis-parses 4+ fences (the greedy
// open backtracks into "3 + info-string" and closes early at a 3-run inside
// the content). A scan is simpler to reason about: find a maximal 3+ run,
// treat the rest of its line as the info string (backtick fences may not
// contain backticks there), then close at the first run of at least the
// opening length on its own line.

/**
 * Indent width of the run at ``runStart``, or null when the run is not a
 * valid fence line start (B1, 2026-08-14). GFM: a fence may be indented by
 * up to 3 spaces; anything more — including a tab, which counts as 4 — is
 * an indented code block, and a run after non-whitespace content is
 * ordinary text. Treating such runs as fences lets body text bypass
 * DOMPurify, so both openers and closers must pass this gate.
 */
function lineStartIndent(md: string, runStart: number): number | null {
  let j = runStart - 1;
  let width = 0;
  while (j >= 0 && md[j] !== "\n") {
    if (md[j] === " ") {
      width += 1;
    } else if (md[j] === "\t") {
      width += 4;
    } else {
      return null;
    }
    j--;
  }
  return width <= 3 ? width : null;
}

/**
 * Stash GFM fenced code blocks (```````/``~~~``, 3+ chars).
 *
 * Scans runs of 3+ backticks or tildes. A run opens a fence only at line
 * start (≤3 leading spaces — B1, 2026-08-14) and only when the rest of its
 * line holds a valid info string (backtick fences: no backticks). The fence
 * closes at the first run of at least the opening length of the same char
 * (B2, 2026-08-14) on its own line with nothing but whitespace after it;
 * shorter runs inside the content are preserved. An unclosed fence is left
 * literal (matching the pre-B4 behavior for ```).
 */
function stashFencedBlocks(
  md: string,
  placeholders: Map<string, string>,
  nextKey: () => string,
): string {
  const parts: string[] = [];
  let pos = 0;
  while (pos < md.length) {
    // Locate the next run of backticks or tildes.
    let i = pos;
    while (i < md.length && md[i] !== "`" && md[i] !== "~") i++;
    if (i >= md.length) {
      parts.push(md.slice(pos));
      break;
    }
    const char = md[i];
    let runEnd = i;
    while (runEnd < md.length && md[runEnd] === char) runEnd++;
    const runLen = runEnd - i;
    if (runLen < 3) {
      // Short run (1-2): inline-code territory, not a fence — pass through.
      parts.push(md.slice(pos, runEnd));
      pos = runEnd;
      continue;
    }
    if (lineStartIndent(md, i) === null) {
      // B1 (2026-08-14): mid-line, over-indented, or tab-indented runs are
      // not fence openers. Pass through as ordinary text so everything after
      // them still reaches DOMPurify.
      parts.push(md.slice(pos, runEnd));
      pos = runEnd;
      continue;
    }
    const lineEnd = md.indexOf("\n", i);
    const restOfLine = md.slice(runEnd, lineEnd === -1 ? md.length : lineEnd);
    if (char === "`" && restOfLine.includes("`")) {
      // Backticks in the info string: not a valid fence opener.
      parts.push(md.slice(pos, runEnd));
      pos = runEnd;
      continue;
    }
    // Closing fence: first run of ≥ the opening length of the same char on
    // its own line, with nothing but whitespace after the run (B2).
    let closeStart: number | null = null;
    let closeEndFound: number | null = null;
    let k = runEnd;
    while (k < md.length) {
      if (md[k] !== char) {
        k++;
        continue;
      }
      let closeEnd = k;
      while (closeEnd < md.length && md[closeEnd] === char) closeEnd++;
      const closeLineEnd = md.indexOf("\n", closeEnd);
      const restOfCloseLine = md.slice(
        closeEnd,
        closeLineEnd === -1 ? md.length : closeLineEnd,
      );
      if (
        closeEnd - k >= runLen &&
        lineStartIndent(md, k) !== null &&
        /^[ \t\r]*$/.test(restOfCloseLine)
      ) {
        closeStart = k;
        closeEndFound = closeEnd;
        break;
      }
      k = closeEnd;
    }
    if (closeStart === null || closeEndFound === null) {
      // Unclosed fence: leave the opening run literal, keep scanning.
      parts.push(md.slice(pos, runEnd));
      pos = runEnd;
      continue;
    }
    // Stash the whole block including the full closing run (which may be
    // longer than the opener under GFM).
    const block = md.slice(i, closeEndFound);
    const key = nextKey();
    placeholders.set(key, block);
    parts.push(md.slice(pos, i)); // text before the fence must survive
    parts.push(key);
    pos = closeEndFound;
  }
  return parts.join("");
}

// Export stash/restore for unit testing without DOMPurify
/**
 * P2-2 (2026-08-18): per-call 随机 nonce。crypto.randomUUID 仅安全上下文
 * （https/localhost）+ 较新浏览器可用；http 非 localhost 部署下 undefined →
 * TypeError → sanitizeMarkdownHtml 崩 → writer 外部内容同步（加载文档/
 * 版本恢复/AI 写入）整体失效。三级降级保持 8 位十六进制（key 仍纯 ASCII
 * letters/digits/hyphens，DOMParser round-trip 不变）。
 */
function randomNonce(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID().slice(0, 8);
  }
  if (c && typeof c.getRandomValues === "function") {
    const bytes = new Uint8Array(4);
    c.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  console.warn("sanitize-markdown: Web Crypto 不可用，回退弱随机 nonce");
  return (Math.random().toString(16).slice(2, 10) + "00000000").slice(0, 8);
}

export function _stashCodeBlocks(md: string): {
  stashed: string;
  placeholders: Map<string, string>;
} {
  const placeholders = new Map<string, string>();
  // Per-call random nonce: keys must be (1) pure ASCII letters/digits/hyphens
  // so DOMParser round-trips them unchanged, and (2) unpredictable so user
  // content can never deterministically collide with a real key (the old
  // \x00 sentinels failed both). The counter keeps keys unique within a doc.
  const nonce = randomNonce();
  let counter = 0;
  const nextKey = (prefix: string) => `${prefix}-${nonce}-${counter++}`;

  // 1. Stash fenced code blocks (``` or ~~~, 3+ chars, close of ≥ opening
  //    length). Must run BEFORE the inline step so fence markers are out of
  //    the way.
  let stashed = stashFencedBlocks(md, placeholders, () => nextKey("ZSFC"));

  // 2. Stash inline code (1-2 backticks, paired). Runs of 3+ are fence
  //    territory (GFM) and are deliberately not matched here, so a stray
  //    mid-line ``` cannot span-stash to a later ``` the way the old
  //    single-backtick regex could.
  const inlineRegex = /(?<!`)(`{1,2})(?!`)([\s\S]*?)\1(?!`)/g;
  stashed = stashed.replace(inlineRegex, (match) => {
    // B3 (2026-08-14): GFM inline code cannot span blank lines. Stashing
    // such a "match" would park ordinary body text (possibly containing
    // HTML) past DOMPurify — leave it unstashed instead.
    // P2-1 (2026-08-18): 空行 = 行间只含空白（CommonMark blank line），
    // 含 "\n \n" 与混合行尾 "\n\r\n"——用正则检测而非枚举字面量。
    if (/\r?\n[ \t]*\r?\n/.test(match)) {
      return match;
    }
    const key = nextKey("ZSIN");
    placeholders.set(key, match);
    return key;
  });

  return { stashed, placeholders };
}

export function _restoreCodeBlocks(
  md: string,
  placeholders: Map<string, string>,
): string {
  let result = md;
  // P1 (2026-08-18): 反向（后插入先还原）。行内反引号对可跨越已 stash 的围栏
  // （撇号误打成反引号 + 后文恰有一对行内代码时，行内正则把含 ZSFC key 的
  // 跨段文本收进 ZSIN 的 value）——正序迭代时 ZSFC 先还原是空操作，ZSIN 最后
  // 展开把 ZSFC key 带回结果且无人再处理 → 代码内容丢失 + 字面 token 残留
  // （会被 onUpdate/getMarkdown 持久化，且再次 sanitize 无法自愈）。反向先展开
  // ZSIN，ZSFC key 回到文本后再被 ZSFC 还原；counter≥10 的前缀撕裂
  // （"ZSIN-x-1" 是 "ZSIN-x-10" 的前缀）同向规避。围栏 value 在行内步骤之前
  // 提取、永不含任何 key；ZSIN value 只含原文（String.replace 不重扫替换产物），
  // 反向安全。
  for (const [key, original] of [...placeholders].reverse()) {
    result = result.split(key).join(original);
  }
  return result;
}

/**
 * Stash code blocks and inline code as placeholders so DOMPurify doesn't
 * strip HTML inside code examples.
 *
 * Delegates to the exported `_stashCodeBlocks` to avoid dual-implementation
 * drift (tests verify the exported copy; production uses the same code).
 */
function stashCodeBlocks(md: string): {
  stashed: string;
  placeholders: Map<string, string>;
} {
  return _stashCodeBlocks(md);
}

/** Restore stashed code blocks from placeholders. Delegates to `_restoreCodeBlocks`. */
function restoreCodeBlocks(
  md: string,
  placeholders: Map<string, string>,
): string {
  return _restoreCodeBlocks(md, placeholders);
}

/**
 * Sanitize markdown content that may contain raw HTML.
 *
 * - Stashes code blocks/inline code as placeholders
 * - Runs DOMPurify on the remaining content (strips script/iframe/on* events)
 * - Preserves img width/height attributes (needed for image sizing)
 * - Restores code blocks (HTML in code examples preserved literally)
 */
export function sanitizeMarkdownHtml(md: string): string {
  const { stashed, placeholders } = stashCodeBlocks(md);

  // Configure DOMPurify: allow img width/height, strip dangerous tags
  const sanitized = getPurify().sanitize(stashed, {
    ADD_ATTR: ["width", "height", "style"],
    FORBID_TAGS: ["script", "iframe", "object", "embed"],
    FORBID_ATTR: ["onerror", "onclick", "onload", "onmouseover"],
  });

  const restored = restoreCodeBlocks(sanitized, placeholders);
  return restored;
}
