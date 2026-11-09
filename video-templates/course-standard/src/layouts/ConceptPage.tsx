import React from "react";
import { useCurrentFrame } from "remotion";
import { THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { Header } from "../components/Header";
import { BodyRegion } from "../components/BodyRegion";
import { SubtitleBar } from "../components/SubtitleBar";

/** 5. 概念讲解页：标题 + 要点逐条入场（bullet 回弹）。 */
export const ConceptPage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const points = c.bullets ?? [];
  return (
    <Page theme={theme}>
      <Header theme={theme} kicker={c.kicker ?? "CONCEPT · 概念讲解"} title={c.title ?? ""}  transitionsOn={transitionsOn} />
<BodyRegion theme={theme}>
        {c.lead ? (
          <div
            style={{
              fontSize: 30,
              color: theme.colors.textSecondary,
              ...A.rise(frame, 8),
            }}
          >
            {c.lead}
          </div>
        ) : null}
        <div style={{ marginTop: 40, display: "flex", flexDirection: "column", gap: 34 }}>
          {points.map((text, i) => (
            <div
              key={i}
              style={{ display: "flex", alignItems: "flex-start", gap: 22, ...A.popBullet(frame, 12 + i * 7) }}
            >
              <div
                style={{
                  marginTop: 6,
                  width: 16,
                  height: 16,
                  borderRadius: 4,
                  background: i === points.length - 1 ? theme.colors.accent : theme.colors.primary,
                  flexShrink: 0,
                  transform: "rotate(45deg)",
                }}
              />
              <div style={{ color: theme.colors.text, fontSize: 36, lineHeight: 1.45 }}>{text}</div>
            </div>
          ))}
        </div>
</BodyRegion>


      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
