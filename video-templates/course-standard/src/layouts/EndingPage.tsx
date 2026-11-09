import React from "react";
import { useCurrentFrame } from "remotion";
import { alpha, THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { SubtitleBar } from "../components/SubtitleBar";
import { VisualSlot } from "../components/VisualSlot";

/** 18. 结尾行动页：本节回顾 + 下集预告 + 行动号召按钮。 */
export const EndingPage: React.FC<LayoutSceneProps> = ({ themeName, content, visualSpec, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  return (
    <Page theme={theme}>
      <div style={{ display: "flex", flex: 1, alignItems: "center" }}>
        <div style={{ flex: 1, paddingRight: 80 }}>
          <div
            style={{
              color: theme.colors.primary,
              fontSize: 24,
              fontWeight: 700,
              letterSpacing: 6,
              ...A.rise(frame, 0),
            }}
          >
            {c.kicker ?? "本节回顾"}
          </div>
          <div
            style={{
              marginTop: 18,
              fontSize: 42,
              fontWeight: 700,
              lineHeight: 1.5,
              color: theme.colors.text,
              ...A.rise(frame, 6),
            }}
          >
            {c.recap ?? ""}
          </div>
          <div
            style={{
              margin: "40px 0",
              height: 1,
              background: theme.colors.line,
              ...A.rise(frame, 12, 18, 10),
            }}
          />
          {c.nextLabel ? (
            <div
              style={{
                display: "inline-flex",
                padding: "8px 22px",
                borderRadius: 999,
                backgroundColor: alpha(theme.colors.accent, 0.12),
                border: `1.5px solid ${theme.colors.accent}`,
                color: theme.colors.accent,
                fontSize: 24,
                fontWeight: 800,
                letterSpacing: 3,
                ...A.rise(frame, 14),
              }}
            >
              {c.nextLabel}
            </div>
          ) : null}
          <div
            style={{
              marginTop: 20,
              fontSize: 44,
              fontWeight: 800,
              color: theme.colors.text,
              ...A.rise(frame, 18),
            }}
          >
            {c.nextTitle ?? ""}
          </div>
          <div
            style={{
              marginTop: 14,
              fontSize: 28,
              color: theme.colors.textSecondary,
              ...A.rise(frame, 22),
            }}
          >
            {c.nextSubtitle ?? ""}
          </div>
          {c.cta ? (
            <div
              style={{
                marginTop: 48,
                display: "inline-flex",
                alignItems: "center",
                gap: 14,
                padding: "22px 48px",
                borderRadius: 999,
                background: `linear-gradient(135deg, ${theme.colors.primary}, ${theme.colors.primaryDeep})`,
                color: theme.colors.onPrimary,
                fontSize: 32,
                fontWeight: 800,
                boxShadow: `0 12px 44px ${alpha(theme.colors.primary, 0.4)}`,
                ...A.rise(frame, 28, 20, 26),
              }}
            >
              {c.cta}
            </div>
          ) : null}
        </div>
        <div style={A.slideX(frame, 10, 24, 70)}>
          <VisualSlot
            theme={theme}
            spec={visualSpec}
            width={520}
            height={640}
            label="AI 素材位"
            note="下集封面 · 竖版"
          />
        </div>
      </div>
      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
