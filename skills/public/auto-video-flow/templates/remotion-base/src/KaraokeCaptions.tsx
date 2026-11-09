import { useMemo } from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";

/* ── Word-level (karaoke) captions ─────────────────────────────
   词级高亮字幕（2026-09-08 T10）：数据源首选豆包 TTS 字级时间戳
   （_doubao_tts.synthesize_with_timestamps，event 364），兜底 whisper.cpp
   对齐。Caption 形状与 @remotion/captions 的 Caption JSON 一致
   （{text, startMs, endMs, timestampMs, confidence}），毫秒为全局时间轴——
   因此本组件与 SubtitleOverlay 一样必须挂在 Composition 根部。
   官方参考：remotion-captions skill 的 display-captions.md。
---------------------------------------------------------------- */

export interface WordCaption {
  text: string;
  startMs: number;
  endMs: number;
  timestampMs: number | null;
  confidence: number;
}

interface CaptionPage {
  words: WordCaption[];
  startMs: number;
  endMs: number;
}

const MAX_WORDS_PER_PAGE = 14;
const PAGE_GAP_MS = 600;

/** 连续词按停顿/长度分页（类似官方 createTikTokStyleCaptions 的合并策略） */
const groupIntoPages = (captions: WordCaption[]): CaptionPage[] => {
  const pages: CaptionPage[] = [];
  let current: CaptionPage | null = null;

  for (const word of captions) {
    const gap = current ? word.startMs - current.endMs : Infinity;
    if (
      !current ||
      gap > PAGE_GAP_MS ||
      current.words.length >= MAX_WORDS_PER_PAGE
    ) {
      current = { words: [word], startMs: word.startMs, endMs: word.endMs };
      pages.push(current);
    } else {
      current.words.push(word);
      current.endMs = word.endMs;
    }
  }
  return pages;
};

// 中英混排：两侧都是拉丁字母/数字时补空格，CJK 之间不补
const needsSpace = (prev: string, next: string): boolean =>
  /[A-Za-z0-9]$/.test(prev) && /^[A-Za-z0-9]/.test(next);

export const KaraokeCaptions: React.FC<{
  captions: WordCaption[];
  fps: number;
}> = ({ captions, fps }) => {
  const frame = useCurrentFrame();
  const nowMs = (frame / fps) * 1000;

  const pages = useMemo(() => groupIntoPages(captions), [captions]);
  const page = pages.find((p) => nowMs >= p.startMs && nowMs < p.endMs + 300);
  if (!page) return null;

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: 80,
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(0, 0, 0, 0.7)",
          fontSize: 32,
          lineHeight: 1.5,
          padding: "10px 24px",
          borderRadius: 10,
          textAlign: "center",
          maxWidth: 1600,
          fontFamily:
            "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif",
        }}
      >
        {page.words.map((word, i) => {
          const isActive = nowMs >= word.startMs && nowMs < word.endMs;
          const isPast = nowMs >= word.endMs;
          const prev = i > 0 ? page.words[i - 1].text : "";
          return (
            <span
              key={`${word.startMs}-${i}`}
              style={{
                color: isActive
                  ? "#FFD700"
                  : isPast
                    ? "#ffffff"
                    : "rgba(255, 255, 255, 0.45)",
                fontWeight: isActive ? 700 : 500,
                transition: "color 80ms linear",
              }}
            >
              {i > 0 && needsSpace(prev, word.text) ? " " : ""}
              {word.text}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
