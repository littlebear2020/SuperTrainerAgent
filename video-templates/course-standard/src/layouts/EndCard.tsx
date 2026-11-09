import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Backdrop } from "../components/Backdrop";
import { LogoBadge } from "../components/LogoBadge";

/** 19. 片尾页：品牌 LOGO + 版权信息 + 关注/订阅引导。 */
export const EndCard: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, logoUrl }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  return (
    <AbsoluteFill style={{ fontFamily: theme.fontFamily, backgroundColor: theme.colors.bg }}>
      <Backdrop theme={theme} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <div style={A.rise(frame, 4, 22, 30)}>
          <LogoBadge theme={theme} size={104} logoUrl={logoUrl} />
        </div>
        {/* T-D2（R7/隐-2）：无品牌不显示——删"启点学院"样例兜底（空则不渲染品牌名行） */}
        {c.brandName ? (
          <div
            style={{
              marginTop: 26,
              fontSize: 40,
              fontWeight: 800,
              color: theme.colors.text,
              letterSpacing: 4,
              ...A.rise(frame, 10),
            }}
          >
            {c.brandName}
          </div>
        ) : null}
        <div
          style={{
            marginTop: 44,
            fontSize: 68,
            fontWeight: 800,
            color: theme.colors.text,
            letterSpacing: 6,
            ...A.rise(frame, 16),
          }}
        >
          {c.title ?? "感谢观看"}
        </div>
        <div
          style={{
            marginTop: 36,
            width: 120,
            height: 6,
            borderRadius: 3,
            background: `linear-gradient(90deg, ${theme.colors.primary}, ${theme.colors.accent})`,
            ...A.rise(frame, 22, 18, 16),
          }}
        />
        {c.follow ? (
          <div style={{ marginTop: 36, fontSize: 34, color: theme.colors.text, ...A.rise(frame, 26) }}>
            {c.follow}
          </div>
        ) : null}
        {c.cta ? (
          <div
            style={{
              marginTop: 20,
              fontSize: 26,
              color: theme.colors.textSecondary,
              ...A.rise(frame, 30),
            }}
          >
            {c.cta}
          </div>
        ) : null}
      </AbsoluteFill>
      {c.copyright ? (
        <div
          style={{
            position: "absolute",
            bottom: 56,
            left: 0,
            right: 0,
            textAlign: "center",
            fontSize: 22,
            color: theme.colors.textDim,
            letterSpacing: 2,
            ...A.fade(frame, 34),
          }}
        >
          {c.copyright}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
