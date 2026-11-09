import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Backdrop } from "../components/Backdrop";
import { LogoBadge } from "../components/LogoBadge";

/** 1. 课程封面：主标题 + 副标题 + 讲师 + 品牌 LOGO。 */
export const CoverPage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, logoUrl }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  return (
    <AbsoluteFill style={{ fontFamily: theme.fontFamily, backgroundColor: theme.colors.bg }}>
      <Backdrop theme={theme} />
      {/* 顶部品牌条 */}
      <div
        style={{
          position: "absolute",
          top: 56,
          left: theme.safe.side,
          display: "flex",
          alignItems: "center",
          gap: 18,
          ...A.rise(frame, 0),
        }}
      >
        <LogoBadge theme={theme} size={60} logoUrl={logoUrl} />
        {/* T-D2（R7/隐-2）：无品牌不显示——删"启点学院"样例兜底（空则不渲染品牌名行） */}
        {c.brandName ? (
          <div style={{ color: theme.colors.text, fontSize: 30, fontWeight: 700 }}>
            {c.brandName}
          </div>
        ) : null}
      </div>
      <div
        style={{
          position: "absolute",
          top: 64,
          right: theme.safe.side,
          color: theme.colors.textDim,
          fontSize: 22,
          letterSpacing: 4,
          ...A.fade(frame, 6),
        }}
      >
        {c.courseNo ?? "COURSE"}
      </div>

      {/* 主视觉区 */}
      <div style={{ position: "absolute", left: 120, top: 300, right: 120 }}>
        {c.series ? (
          <div
            style={{
              display: "inline-flex",
              padding: "10px 26px",
              borderRadius: 999,
              border: `1.5px solid ${theme.colors.primary}`,
              color: theme.colors.primary,
              fontSize: 26,
              fontWeight: 600,
              letterSpacing: 2,
              ...A.rise(frame, 6),
            }}
          >
            {c.series}
          </div>
        ) : null}
        <div
          style={{
            marginTop: 36,
            fontSize: 96,
            fontWeight: 800,
            color: theme.colors.text,
            lineHeight: 1.15,
            letterSpacing: 2,
            ...A.rise(frame, 12),
          }}
        >
          {c.title ?? ""}
        </div>
        <div
          style={{
            marginTop: 34,
            width: 132,
            height: 8,
            borderRadius: 4,
            background: `linear-gradient(90deg, ${theme.colors.accent}, ${theme.colors.primary})`,
            ...A.rise(frame, 18, 20, 20),
          }}
        />
        {c.subtitle ? (
          <div
            style={{
              marginTop: 30,
              fontSize: 38,
              color: theme.colors.textSecondary,
              lineHeight: 1.5,
              ...A.rise(frame, 22),
            }}
          >
            {c.subtitle}
          </div>
        ) : null}
        {c.instructor ? (
          <div
            style={{
              marginTop: 56,
              display: "flex",
              alignItems: "center",
              gap: 18,
              ...A.rise(frame, 28),
            }}
          >
            <div
              style={{
                width: 64,
                height: 64,
                borderRadius: "50%",
                background: theme.colors.bgElevated,
                border: `2px solid ${theme.colors.line}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: theme.colors.text,
                fontSize: 28,
                fontWeight: 700,
              }}
            >
              {c.instructor.initial}
            </div>
            <div style={{ color: theme.colors.text, fontSize: 30 }}>
              {c.instructor.name}
              <span style={{ color: theme.colors.textDim, fontSize: 24, marginLeft: 16 }}>
                {c.instructor.title}
              </span>
            </div>
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
