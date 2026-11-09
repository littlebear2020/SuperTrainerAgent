import React from "react";
import { useCurrentFrame } from "remotion";
import { alpha, THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps, renderRichText } from "../content";
import { Page } from "../components/Page";
import { Header } from "../components/Header";
import { BodyRegion } from "../components/BodyRegion";
import { SubtitleBar } from "../components/SubtitleBar";

/** 15. 案例页：背景 / 做法 / 结果 三段式卡片（按位置序着色，metric 卡用大数字）。 */
export const CasePage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const cards = c.cards ?? [];
  return (
    <Page theme={theme}>
      <Header theme={theme} kicker={c.kicker ?? "CASE STUDY · 实战案例"} title={c.title ?? ""} titleSize={64}  transitionsOn={transitionsOn} />
<BodyRegion theme={theme}>
        <div style={{ display: "flex", gap: 32, flex: 1 }}>
          {cards.map((card, i) => {
            // 位置序决定配色：0=中性（背景）、1=主色（做法）、2=强调色（结果）
            const styleKind = i === 1 ? "primary" : i === 2 ? "accent" : "neutral";
            const labelBg =
              styleKind === "primary"
                ? theme.colors.primary
                : styleKind === "accent"
                  ? theme.colors.accent
                  : alpha(theme.colors.textSecondary, 0.14);
            const labelColor =
              styleKind === "primary"
                ? theme.colors.onPrimary
                : styleKind === "accent"
                  ? theme.colors.onAccent
                  : theme.colors.textSecondary;
            return (
              <div
                key={i}
                style={{
                  flex: 1,
                  borderRadius: 24,
                  backgroundColor: theme.colors.card,
                  border: `1px solid ${theme.colors.line}`,
                  padding: "36px 34px",
                  ...A.rise(frame, 10 + i * 8, 24, 40),
                }}
              >
                <div
                  style={{
                    display: "inline-flex",
                    padding: "8px 26px",
                    borderRadius: 999,
                    background: labelBg,
                    color: labelColor,
                    fontSize: 24,
                    fontWeight: 800,
                  }}
                >
                  {card.label}
                </div>
                <div style={{ marginTop: 28 }}>
                  {card.metric ? (
                    <div style={{ fontSize: 64, fontWeight: 900, color: theme.colors.accent, lineHeight: 1.1 }}>
                      {card.metric}
                    </div>
                  ) : null}
                  <div style={{ fontSize: 28, lineHeight: 1.65, color: theme.colors.textSecondary, marginTop: card.metric ? 14 : 0 }}>
                    {renderRichText(card.body, theme.colors, theme.colors.textSecondary)}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
</BodyRegion>


      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
