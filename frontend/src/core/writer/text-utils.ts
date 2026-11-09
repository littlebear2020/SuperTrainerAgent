/**
 * writer 文本处理共享件（2026-10-06 近重复合并批）。
 *
 * isCJKWordChar 原在 count-words.ts 与 compute-diff.ts 逐字双份（后者挂
 * "Mirrors" 互抄注释）；markdown strip 原两处独立实现（计数 15 条规则 /
 * TTS 8 条规则）。收敛：CJK 判定单一来源；strip 共享规则表（计数完整顺序
 * 为基准），「整删替换符」以 {S} 占位 —— 计数路径传 " "（词距语义）、
 * TTS 路径传 ""（朗读不吃多余空格）。
 */

/**
 * A CJK "word character" — an ideograph, kana, or hangul that counts as one.
 * Covers Chinese 汉字, Japanese kana/kanji, Korean hangul, and extensions.
 * CJK punctuation is excluded (handled separately so it is not counted).
 */
export function isCJKWordChar(code: number): boolean {
  return (
    (code >= 0x4e00 && code <= 0x9fff) || // CJK Unified Ideographs
    (code >= 0x3400 && code <= 0x4dbf) || // CJK Unified Ideographs Extension A
    (code >= 0x20000 && code <= 0x2ffff) || // CJK Unified Ideographs Extension B+ (supplementary plane)
    (code >= 0xf900 && code <= 0xfaff) || // CJK Compatibility Ideographs
    (code >= 0x3040 && code <= 0x30ff) || // Hiragana + Katakana
    (code >= 0x31f0 && code <= 0x31ff) || // Katakana Phonetic Extensions
    (code >= 0x3100 && code <= 0x312f) || // Bopomofo
    (code >= 0xac00 && code <= 0xd7af) || // Hangul Syllables
    (code >= 0x1100 && code <= 0x11ff) || // Hangul Jamo
    (code >= 0x3130 && code <= 0x318f) // Hangul Compatibility Jamo
  );
}

/** 规则表：{S} = 整删替换符（计数 " " / TTS ""）。顺序敏感（bold 先于 italic 等）。 */
const MARKDOWN_STRIP_RULES: ReadonlyArray<readonly [RegExp, string]> = [
  // Fenced code blocks (```…```) — remove entirely (markup, not prose).
  [/```[\s\S]*?```/g, "{S}"],
  // Inline code `code` — keep content, drop backticks.
  [/`([^`]+)`/g, "$1"],
  // Images ![alt](url) — keep alt text.
  [/!\[([^\]]*)\]\([^)]*\)/g, "$1"],
  // Reference links [text][ref] — keep text.
  [/\[([^\]]+)\]\[[^\]]*\]/g, "$1"],
  // Inline links [text](url) — keep text.
  [/\[([^\]]+)\]\([^)]*\)/g, "$1"],
  // ATX headings: leading #{1,6} + optional space (a lone `#` line → "").
  [/^#{1,6}\s?/gm, ""],
  // Blockquotes: leading > .
  [/^>\s?/gm, ""],
  // Unordered list markers: leading - * + + space.
  [/^[-*+]\s+/gm, ""],
  // Ordered list markers: leading 1. or 1) + space.
  [/^\d+[.)]\s+/gm, ""],
  // Bold **text** / __text__ — keep text.
  [/\*\*(.+?)\*\*/g, "$1"],
  [/__(.+?)__/g, "$1"],
  // Strikethrough ~~text~~ — keep text.
  [/~~(.+?)~~/g, "$1"],
  // Italic *text* — keep text (after bold so ** is consumed first).
  [/\*(.+?)\*/g, "$1"],
  // Horizontal rules / setext underlines: a line of 3+ of - * _ = ~.
  [/^[-*_=~]{3,}\s*$/gm, "{S}"],
  // Table separator rows (| : - only) → drop char.
  [/^\|?[\s:|-]+$/gm, "{S}"],
  // Table pipes → drop char (cell text preserved).
  [/\|/g, "{S}"],
];

/**
 * 共享 markdown 剥离引擎。`drop` = 整删规则的替换符：
 * - " "（计数）：删掉的结构留一个空格作词距
 * - ""（TTS ）：删掉的结构不留空格（朗读不吃多余停顿）
 */
export function stripMarkdown(text: string, drop: "" | " " = ""): string {
  let out = text;
  for (const [re, template] of MARKDOWN_STRIP_RULES) {
    out = out.replace(re, template.replaceAll("{S}", drop));
  }
  return out;
}
