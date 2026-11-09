import React from "react";
import { useCurrentFrame } from "remotion";
import { THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { Header } from "../components/Header";
import { BodyRegion } from "../components/BodyRegion";
import { SubtitleBar } from "../components/SubtitleBar";

/** 2. 学习目标页：序号勾选图标 + 逐条入场。 */
export const ObjectivesPage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const objectives = c.bullets ?? [];
  return (
    <Page theme={theme}>
      <Header theme={theme} kicker={c.kicker ?? "OBJECTIVES · 学习目标"} title={c.title ?? ""}  transitionsOn={transitionsOn} />
<BodyRegion theme={theme}>
        <div
          style={{
            padding: "46px 56px",
            borderRadius: 24,
            backgroundColor: theme.colors.card,
            border: `1px solid ${theme.colors.line}`,
            display: "flex",
            flexDirection: "column",
            gap: 36,
          }}
        >
          {objectives.map((text, i) => (
            <div
              key={i}
              style={{ display: "flex", alignItems: "center", gap: 26, ...A.popBullet(frame, 8 + i * 7) }}
            >
              <div
                style={{
                  width: 64,
                  height: 64,
                  borderRadius: "50%",
                  background: theme.colors.primary,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: theme.colors.onPrimary,
                  fontSize: 34,
                  fontWeight: 800,
                  flexShrink: 0,
                }}
              >
                ✓
              </div>
              <div style={{ color: theme.colors.text, fontSize: 36, lineHeight: 1.4 }}>{text}</div>
            </div>
          ))}
        </div>
</BodyRegion>


      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
