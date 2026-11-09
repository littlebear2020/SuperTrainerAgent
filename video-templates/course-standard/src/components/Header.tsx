import React from "react";
import { useCurrentFrame } from "remotion";
import { Theme } from "../theme";
import { animKit } from "../lib/anim";

/**
 * 内容页统一页眉：kicker（小标签）+ 主标题 + 强调色竖条。
 *
 * T-B2（R8，2026-09-09 feedback-round1）：入场动效走 animKit(transitionsOn)——
 * 修前无条件 rise()，转场模式下页眉入场与 TransitionSeries 淡入叠加成"闪切"
 * （与 F1 字幕闪烁同源观感）。transitionsOn 缺省（画廊等无转场场景）时动效照常。
 */
export const Header: React.FC<{
  theme: Theme;
  kicker: string;
  title: string;
  titleSize?: number;
  delay?: number;
  transitionsOn?: boolean;
}> = ({ theme, kicker, title, titleSize = theme.type.pageTitle, delay = 0, transitionsOn }) => {
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  return (
    <div style={{ fontFamily: theme.fontFamily }}>
      <div
        style={{
          color: theme.colors.primary,
          fontSize: 24,
          fontWeight: 700,
          letterSpacing: 6,
          ...A.rise(frame, delay),
        }}
      >
        {kicker}
      </div>
      <div
        style={{
          marginTop: 16,
          display: "flex",
          alignItems: "center",
          gap: 22,
          ...A.rise(frame, delay + 4),
        }}
      >
        <div
          style={{
            width: 8,
            height: Math.round(titleSize * 0.8),
            borderRadius: 4,
            background: theme.colors.accent,
            flexShrink: 0,
          }}
        />
        <div
          style={{
            fontSize: titleSize,
            color: theme.colors.text,
            fontWeight: 800,
            lineHeight: 1.15,
          }}
        >
          {title}
        </div>
      </div>
    </div>
  );
};
