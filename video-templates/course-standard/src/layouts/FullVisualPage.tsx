import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { alpha, THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Backdrop } from "../components/Backdrop";
import { VisualSlot } from "../components/VisualSlot";

/**
 * 8. 全幅视觉页：整幅视觉素材 + 底部标题条。
 * 素材缺失时回退 M0 占位观感（网格渐变 + 图标文案），不炸渲染。
 */
export const FullVisualPage: React.FC<LayoutSceneProps> = ({ themeName, content, visualSpec, transitionsOn }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  return (
    <AbsoluteFill style={{ fontFamily: theme.fontFamily, backgroundColor: theme.colors.bg }}>
      <Backdrop theme={theme} />
      {/* 全幅视觉：素材优先，缺省回退占位观感 */}
      {visualSpec && visualSpec.path && visualSpec.kind !== "none" && visualSpec.kind !== "graphic" ? (
        <AbsoluteFill>
          <VisualSlot theme={theme} spec={visualSpec} width="100%" height="100%" radius={0} />
        </AbsoluteFill>
      ) : (
        <AbsoluteFill
          style={{
            backgroundColor: theme.colors.visualFrom,
            backgroundImage: `
              linear-gradient(${theme.colors.gridLine} 1px, transparent 1px),
              linear-gradient(90deg, ${theme.colors.gridLine} 1px, transparent 1px),
              linear-gradient(135deg, ${theme.colors.visualFrom} 0%, ${theme.colors.visualVia} 55%, ${theme.colors.visualTo} 150%)`,
            backgroundSize: "80px 80px, 80px 80px, cover",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <svg
            width={110}
            height={110}
            viewBox="0 0 24 24"
            fill="none"
            stroke={theme.colors.textSecondary}
            strokeWidth="1.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="3" width="18" height="18" rx="3" />
            <circle cx="9" cy="9" r="2" />
            <path d="M21 15l-4.5-4.5L6 21" />
          </svg>
          <div style={{ marginTop: 24, color: theme.colors.text, fontSize: 40, fontWeight: 700 }}>
            AI 素材位 · 全幅视觉
          </div>
          <div style={{ marginTop: 12, color: theme.colors.textSecondary, fontSize: 26 }}>
            {c.note ?? "建议 1920 × 1080 · 全幅视觉素材"}
          </div>
        </AbsoluteFill>
      )}
      {/* 底部标题条（深色渐变遮罩） */}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          padding: "130px 60px 64px",
          background: `linear-gradient(180deg, rgba(0, 0, 0, 0) 0%, ${theme.colors.overlayTo} 32%)`,
        }}
      >
        <div
          style={{
            color: theme.colors.accent,
            fontSize: 24,
            fontWeight: 700,
            letterSpacing: 6,
            ...A.rise(frame, 10),
          }}
        >
          {c.kicker ?? "FULL VISUAL · 全幅画面"}
        </div>
        <div
          style={{
            marginTop: 14,
            fontSize: 54,
            fontWeight: 800,
            color: theme.colors.subtitleText,
            ...A.rise(frame, 14),
          }}
        >
          {c.title ?? ""}
        </div>
        {c.subtitle ? (
          <div
            style={{
              marginTop: 12,
              fontSize: 28,
              color: alpha(theme.colors.subtitleText, 0.72),
              ...A.rise(frame, 18),
            }}
          >
            {c.subtitle}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
