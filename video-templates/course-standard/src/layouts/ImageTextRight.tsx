import React from "react";
import { useCurrentFrame } from "remotion";
import { THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { SubtitleBar } from "../components/SubtitleBar";
import { VisualSlot } from "../components/VisualSlot";

/** 7. 图文页（右图左文）。 */
export const ImageTextRight: React.FC<LayoutSceneProps> = ({ themeName, content, visualSpec, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const points = c.bullets ?? [];
  return (
    <Page theme={theme}>
      <div style={{ display: "flex", flex: 1, alignItems: "center" }}>
        <div style={{ flex: 1, paddingRight: 72 }}>
          <div
            style={{
              color: theme.colors.primary,
              fontSize: 24,
              fontWeight: 700,
              letterSpacing: 6,
              ...A.rise(frame, 8),
            }}
          >
            {c.kicker ?? "图文讲解"}
          </div>
          <div
            style={{
              marginTop: 16,
              fontSize: theme.type.sectionTitle,
              fontWeight: 800,
              color: theme.colors.text,
              lineHeight: 1.2,
              ...A.rise(frame, 12),
            }}
          >
            {c.title ?? ""}
          </div>
          <div style={{ marginTop: 40, display: "flex", flexDirection: "column", gap: 28 }}>
            {points.map((text, i) => (
              <div
                key={i}
                style={{ display: "flex", alignItems: "flex-start", gap: 18, ...A.popBullet(frame, 16 + i * 7, 20, 40) }}
              >
                <div
                  style={{
                    marginTop: 8,
                    width: 12,
                    height: 12,
                    borderRadius: "50%",
                    background: theme.colors.primary,
                    flexShrink: 0,
                  }}
                />
                <div style={{ color: theme.colors.textSecondary, fontSize: 32, lineHeight: 1.5 }}>
                  {text}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div style={A.slideX(frame, 4, 24, 70)}>
          <VisualSlot theme={theme} spec={visualSpec} width={800} height={640} label="AI 素材位" note="上下文交接示意 · 建议 5:4" />
        </div>
      </div>
      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
