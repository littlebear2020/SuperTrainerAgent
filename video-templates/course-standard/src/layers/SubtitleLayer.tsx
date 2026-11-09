import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { Theme } from "../theme";
import { CaptionWord, SubtitleStyleSpec } from "../course-props";

/**
 * 字幕层（挂 Composition 根部，全局帧——严禁放进场景 Sequence/TransitionSeries）。
 * 逐词 karaoke 高亮（karaokeEnabled=false 时句级不高亮）。
 * 时间轴：caption 的 startMs/endMs 是**全局毫秒**（gateway 侧已折算场景偏移），
 * 按帧换算 contract：ms = frame / fps * 1000。
 *
 * 行分组：按词序贪心装箱（maxCharsPerLine，默认 18 字/行）；**场景边界强制换行**
 * （T-B2 修跨场景拼行——修前行尾的上一场景词与行首的下一场景词混在一行）。
 * 展示行 = 当前活动词所在行。
 *
 * T-B2（F1，2026-09-09 feedback-round1）hold-until-next：词间隙不再让字幕条
 * 整条消失（修前"当前帧不在任何词 [startMs,endMs) 内"即 return null——词间隙/
 * 场景间隙整条闪烁）。规则：
 *   ① now 落在某词 [startMs,endMs) → 该词活动；
 *   ② 否则取 startMs ≤ now 的最近词（已播的最后一个词）：
 *      - sceneRanges 可查 → now 仍在该词所属场景区间内 → hold 该词所在行
 *        （场景尾部静默/句间停顿字幕保持在场；转场窗与下一场景首词前让位 null）；
 *      - 无场景域（存量 props）→ 直接 hold（词间隙修复向前兼容）；
 *   ③ 无已播词（如 intro 段）→ null。
 */
export const SubtitleLayer: React.FC<{
  theme: Theme;
  captions: CaptionWord[];
  subtitleStyle?: SubtitleStyleSpec;
  sceneRangesMs?: { startMs: number; endMs: number }[];
}> = ({ theme, captions, subtitleStyle, sceneRangesMs }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (!captions.length) return null;

  const karaoke = subtitleStyle?.karaokeEnabled !== false; // D6 默认开
  const maxChars = subtitleStyle?.maxCharsPerLine ?? 18;
  // T-E9：variant 渲染（缺省 = default 深底条，与 M0 基线像素一致）
  const variant = subtitleStyle?.variant ?? "default";
  // none = 不显示字幕（course-props.ts 契约）。gateway 侧任务级 "none" 走不产
  // captions，但项目级 subtitleStyle.variant 可直达（UI 已过滤，API 可达）——
  // 防御性短路，否则落到下方 outline 分支照常渲染。
  if (variant === "none") return null;
  const fontSize =
    subtitleStyle?.fontSize ?? (variant === "bold" ? 44 : variant === "minimal" ? 32 : theme.type.subtitle);

  const nowMs = (frame / fps) * 1000;

  // 贪心分行（场景边界强制换行）
  const lines: CaptionWord[][] = [];
  let cur: CaptionWord[] = [];
  let curLen = 0;
  let curScene: number | undefined;
  for (const w of captions) {
    const sc = w.sceneIndex;
    if (sc !== curScene) {
      if (cur.length) lines.push(cur);
      cur = [];
      curLen = 0;
      curScene = sc;
    }
    if (curLen + w.word.length > maxChars && cur.length) {
      lines.push(cur);
      cur = [];
      curLen = 0;
    }
    cur.push(w);
    curLen += w.word.length;
  }
  if (cur.length) lines.push(cur);

  // 找当前词（严格落在 [startMs,endMs) 内；容差半帧防边界漏字）
  const tolerance = 500 / fps;
  let activeIdx = -1;
  for (let i = 0; i < captions.length; i++) {
    const w = captions[i];
    if (nowMs >= w.startMs - tolerance && nowMs < w.endMs + tolerance) {
      activeIdx = i;
      break;
    }
  }
  // hold-until-next：词间隙保持在场（限定当前场景内）
  let karaokeIdx = activeIdx;
  if (activeIdx < 0) {
    let lastStarted = -1;
    for (let i = 0; i < captions.length; i++) {
      if (captions[i].startMs <= nowMs) lastStarted = i;
      else break;
    }
    if (lastStarted >= 0) {
      const w = captions[lastStarted];
      const range = w.sceneIndex != null ? sceneRangesMs?.[w.sceneIndex] : undefined;
      if (range ? nowMs < range.endMs : true) {
        activeIdx = lastStarted; // 展示该词所在行
        karaokeIdx = -1; // 间隙不高亮任何词（hold 态）
      }
    }
  }
  if (activeIdx < 0) return null;

  // 活动词所在行
  let consumed = 0;
  let activeLine = lines[0];
  for (const line of lines) {
    if (activeIdx < consumed + line.length) {
      activeLine = line;
      break;
    }
    consumed += line.length;
  }

  return (
    <div
      style={{
        position: "absolute",
        left: theme.safe.side,
        right: theme.safe.side,
        bottom: 52,
        display: "flex",
        justifyContent: "center",
        fontFamily: theme.fontFamily,
        pointerEvents: "none",
      }}
    >
      <div
        style={
          variant === "default"
            ? {
                maxWidth: 1520,
                padding: "16px 44px",
                borderRadius: 18,
                backgroundColor: theme.colors.subtitleBg,
                border: `1px solid ${theme.colors.subtitleBorder}`,
                color: theme.colors.subtitleText,
                fontSize,
                lineHeight: 1.4,
                textAlign: "center",
              }
            : variant === "bold"
              ? {
                  maxWidth: 1600,
                  color: "#FFFFFF",
                  fontSize,
                  fontWeight: 800,
                  lineHeight: 1.35,
                  textAlign: "center",
                  textShadow: "0 2px 14px rgba(0,0,0,0.65)",
                }
              : variant === "minimal"
                ? {
                    maxWidth: 1520,
                    color: theme.colors.text,
                    fontSize,
                    lineHeight: 1.4,
                    textAlign: "center",
                    textShadow: "0 1px 6px rgba(0,0,0,0.5)",
                  }
                : {
                    /* outline：无底条描边字（高对比） */
                    maxWidth: 1600,
                    color: "#FFFFFF",
                    fontSize,
                    fontWeight: 700,
                    lineHeight: 1.35,
                    textAlign: "center",
                    WebkitTextStroke: "5px rgba(10,14,24,0.9)",
                    paintOrder: "stroke fill",
                  }
        }
      >
        {activeLine.map((w, i) => {
          const isActive = karaoke && karaokeIdx >= 0 && captions.indexOf(w) === karaokeIdx;
          return (
            <span
              key={i}
              style={{
                color: isActive ? theme.colors.accent : undefined,
                fontWeight: isActive ? 800 : undefined,
              }}
            >
              {w.word}
            </span>
          );
        })}
      </div>
    </div>
  );
};
