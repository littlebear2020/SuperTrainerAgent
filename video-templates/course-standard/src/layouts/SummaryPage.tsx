import React from "react";
import { useCurrentFrame } from "remotion";
import { alpha, THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { Header } from "../components/Header";
import { BodyRegion } from "../components/BodyRegion";
import { SubtitleBar } from "../components/SubtitleBar";

/** 16. 小结页：勾选框清单（与概念页的 bullet 视觉区分）。 */
export const SummaryPage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const points = c.bullets ?? [];
  return (
    <Page theme={theme}>
      <Header theme={theme} kicker={c.kicker ?? "SUMMARY · 小结"} title={c.title ?? ""}  transitionsOn={transitionsOn} />
<BodyRegion theme={theme}>
        <div
          style={{
            borderRadius: 24,
            backgroundColor: theme.colors.card,
            border: `1px solid ${theme.colors.line}`,
            overflow: "hidden",
          }}
        >
          {points.map((text, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 26,
                padding: "30px 48px",
                borderBottom: i < points.length - 1 ? `1px solid ${theme.colors.line}` : "none",
                ...A.popBullet(frame, 8 + i * 7),
              }}
            >
              <div
                style={{
                  width: 46,
                  height: 46,
                  borderRadius: 12,
                  border: `2px solid ${theme.colors.primary}`,
                  backgroundColor: alpha(theme.colors.primary, 0.16),
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: theme.colors.primary,
                  fontSize: 26,
                  fontWeight: 800,
                  flexShrink: 0,
                }}
              >
                ✓
              </div>
              <div style={{ fontSize: 34, color: theme.colors.text, lineHeight: 1.4 }}>{text}</div>
            </div>
          ))}
        </div>
</BodyRegion>


      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
