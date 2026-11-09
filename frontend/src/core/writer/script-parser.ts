/**
 * Parse a Markdown video script into structured scenes.
 *
 * A script Markdown follows the template:
 * ```
 * # 视频标题
 *
 * ## 场景 1
 *
 * **画面**：...
 *
 * **解说词**：...
 * ```
 *
 * Scenes are delimited by `## ` headings. Within each scene, `**画面**：`
 * and `**解说词**：` (colon may be fullwidth ： or ascii :) mark the visual
 * description and narration respectively. Any text not matching these labels
 * is appended to narration as a fallback so TTS never loses content.
 */

import { stripMarkdown } from "./text-utils";

/** A single scene parsed from a Markdown script. */
export interface ScriptScene {
  /** Zero-based scene index. */
  index: number;
  /** Heading text without the `## ` prefix (e.g. "场景 1"). */
  title: string;
  /** Visual/画面 description, if present. Not sent to TTS. */
  visual: string | null;
  /** Narration/解说词 text — this is what TTS reads. */
  narration: string;
  /** Estimated duration in seconds at ~3 chars/s (Chinese speech rate). */
  estimatedDuration: number;
}

/** Parse Markdown script text into scenes. Returns [] if no scenes found. */
export function parseScriptScenes(markdown: string): ScriptScene[] {
  if (!markdown?.trim()) return [];

  const lines = markdown.split("\n");
  const scenes: ScriptScene[] = [];
  let current: {
    title: string;
    visual: string[] | null;
    narration: string[];
    extra: string[];
  } | null = null;

  const CHARS_PER_SECOND = 3;

  const pushCurrent = () => {
    if (!current) return;
    // If no explicit 解说词 label was found, treat extra lines as narration
    const narration =
      current.narration.length > 0
        ? current.narration.join("\n").trim()
        : current.extra.join("\n").trim();
    const cleanNarration = stripMarkdownForTts(narration);
    scenes.push({
      index: scenes.length,
      title: current.title,
      visual: current.visual?.join("\n").trim() ?? null,
      narration: cleanNarration,
      estimatedDuration: Math.max(
        1,
        Math.ceil(cleanNarration.length / CHARS_PER_SECOND),
      ),
    });
  };

  for (const line of lines) {
    const sceneMatch = /^##\s+(.+)$/.exec(line);
    if (sceneMatch) {
      pushCurrent();
      current = {
        title: (sceneMatch[1] ?? "").trim(),
        visual: null,
        narration: [],
        extra: [],
      };
      continue;
    }

    if (!current) {
      // Skip preamble (title, metadata) before first scene
      continue;
    }

    // Match labels: **画面**： or **解说词**： (fullwidth or ascii colon, optional space)
    const visualMatch =
      /^\*\*(?:画面|视觉|画面描述|场景描述)\*\*[：:]\s*(.*)$/.exec(line);
    const narrationMatch =
      /^\*\*(?:解说词|旁白|台词|配音|语音)\*\*[：:]\s*(.*)$/.exec(line);

    if (visualMatch) {
      current.visual ??= [];
      const text = visualMatch[1] ?? "";
      if (text.trim()) current.visual.push(text.trim());
    } else if (narrationMatch) {
      const text = narrationMatch[1] ?? "";
      if (text.trim()) current.narration.push(text.trim());
    } else if (line.trim()) {
      // Continuation of current label or unlabeled content
      if (current.visual !== null && current.narration.length === 0) {
        // We're in a visual block that hasn't transitioned to narration yet
        current.visual.push(line.trim());
      } else {
        current.extra.push(line.trim());
      }
    }
  }

  pushCurrent();
  return scenes;
}

/**
 * Lightweight Markdown stripping for TTS readability.
 * Removes heading markers, bold/italic, code fences, and link syntax.
 * Does NOT strip HTML tags (those are handled by backend strip_markdown).
 */
export function stripMarkdownForTts(text: string): string {
  // 规则表收敛至 text-utils.ts（TTS 路径整删替换符 = ""，规则为其 8 条的超集）
  return stripMarkdown(text, "").trim();
}

/** Concatenate all scene narrations into a single TTS-ready string. */
export function scenesToNarrationText(scenes: ScriptScene[]): string {
  return scenes
    .map((s) => s.narration)
    .filter(Boolean)
    .join("\n\n");
}

/** Total estimated duration across all scenes. */
export function totalEstimatedDuration(scenes: ScriptScene[]): number {
  return scenes.reduce((sum, s) => sum + s.estimatedDuration, 0);
}
