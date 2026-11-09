import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { alpha, THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Backdrop } from "../components/Backdrop";

/**
 * 4. 章节过渡页：超大章节数字 + 章节标题 + 简短导语。
 * 数字与标题块作为整体在画面安全区内垂直居中（底部 160px 字幕预留区不计入）。
 */
export const ChapterDivider: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const no = c.chapterNo ?? 1;
  const total = c.chapterTotal;
  return (
    <AbsoluteFill style={{ fontFamily: theme.fontFamily, backgroundColor: theme.colors.bg }}>
      <Backdrop theme={theme} />
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: 0,
          bottom: theme.safe.bottom,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          paddingLeft: 120,
          paddingRight: 100,
        }}
      >
        {/* 章节标题块 */}
        <div style={{ maxWidth: 900 }}>
          <div
            style={{
              display: "inline-flex",
              padding: "8px 24px",
              borderRadius: 999,
              backgroundColor: alpha(theme.colors.primary, 0.14),
              border: `1.5px solid ${theme.colors.primary}`,
              color: theme.colors.primary,
              fontSize: 26,
              fontWeight: 700,
              letterSpacing: 3,
              ...A.rise(frame, 8),
            }}
          >
            {total ? `第 ${no} 章 / 共 ${total} 章` : `第 ${no} 章`}
          </div>
          <div
            style={{
              marginTop: 30,
              fontSize: 84,
              fontWeight: 800,
              color: theme.colors.text,
              lineHeight: 1.2,
              ...A.rise(frame, 14),
            }}
          >
            {c.title ?? ""}
          </div>
          <div
            style={{
              marginTop: 26,
              width: 110,
              height: 7,
              borderRadius: 4,
              background: theme.colors.accent,
              ...A.rise(frame, 20, 20, 20),
            }}
          />
          <div
            style={{
              marginTop: 26,
              fontSize: 34,
              color: theme.colors.textSecondary,
              lineHeight: 1.55,
              ...A.rise(frame, 24),
            }}
          >
            {c.lead ?? ""}
          </div>
        </div>
        {/* 超大章节数字（品牌渐变文字） */}
        <div
          style={{
            fontSize: 460,
            fontWeight: 900,
            lineHeight: 1,
            flexShrink: 0,
            background: `linear-gradient(180deg, ${theme.colors.primary}, ${theme.colors.accent})`,
            WebkitBackgroundClip: "text",
            backgroundClip: "text",
            color: "transparent",
            opacity: 0.9,
            transform: `scale(${transitionsOn ? 1 : 1.06 - 0.06 * Math.min(1, frame / 30)})`,
            ...A.fade(frame, 0, 26),
          }}
        >
          {String(no).padStart(2, "0")}
        </div>
      </div>
    </AbsoluteFill>
  );
};
