import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps, renderRichText } from "../content";
import { Backdrop } from "../components/Backdrop";
import { VisualSlot } from "../components/VisualSlot";

/**
 * 14. 金句强调页：左侧金句内容（serif 引号装饰 + 金句 + 出处），
 * 右侧 AI 配图位（4:5 竖版）。
 */
export const QuotePage: React.FC<LayoutSceneProps> = ({ themeName, content, visualSpec, transitionsOn }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  return (
    <AbsoluteFill style={{ fontFamily: theme.fontFamily, backgroundColor: theme.colors.bg }}>
      <Backdrop theme={theme} />
      <AbsoluteFill
        style={{ padding: `64px ${theme.safe.side + 20}px ${theme.safe.bottom}px` }}
      >
        <div style={{ display: "flex", alignItems: "center", flex: 1, gap: 90 }}>
          {/* 左：金句内容 */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center" }}>
            <div
              style={{
                fontFamily: theme.serifFamily,
                fontSize: 260,
                lineHeight: 0.6,
                height: 112,
                color: theme.colors.primary,
                opacity: 0.55,
                ...A.fade(frame, 2, 24),
              }}
            >
              “
            </div>
            <div
              style={{
                fontFamily: theme.serifFamily,
                fontSize: 60,
                fontWeight: 700,
                lineHeight: 1.6,
                color: theme.colors.text,
                ...A.rise(frame, 10, 26, 40),
              }}
            >
              {c.quote ? renderRichText(c.quote, theme.colors, theme.colors.text) : null}
            </div>
            <div
              style={{
                marginTop: 44,
                width: 96,
                height: 6,
                borderRadius: 3,
                background: theme.colors.accent,
                ...A.rise(frame, 20, 18, 20),
              }}
            />
            <div
              style={{
                marginTop: 24,
                fontSize: 28,
                color: theme.colors.textSecondary,
                letterSpacing: 2,
                ...A.rise(frame, 24),
              }}
            >
              {c.source ?? ""}
            </div>
          </div>
          {/* 右：AI 配图位 */}
          <div style={A.slideX(frame, 8, 24, 60)}>
            <VisualSlot
              theme={theme}
              spec={visualSpec}
              width={540}
              height={675}
              label="金句配图位"
              note="AI 素材位 · 建议 4:5 竖版"
            />
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
