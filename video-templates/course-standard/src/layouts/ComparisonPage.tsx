import React from "react";
import { useCurrentFrame } from "remotion";
import { alpha, Theme, THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { Header } from "../components/Header";
import { BodyRegion } from "../components/BodyRegion";
import { SubtitleBar } from "../components/SubtitleBar";

const CompareCard: React.FC<{
  theme: Theme;
  frame: number;
  label: string;
  items: string[];
  good: boolean;
  transitionsOn: boolean;
  delay: number;
}> = ({ theme, frame, label, items, good, transitionsOn, delay }) => {
  const A = animKit(transitionsOn);
  return (
    <div
      style={{
        flex: 1,
        borderRadius: 24,
        padding: "36px 40px",
        backgroundColor: good ? alpha(theme.colors.primary, 0.1) : theme.colors.card,
        border: `2px solid ${good ? theme.colors.primary : theme.colors.line}`,
        boxShadow: good ? `0 0 60px ${alpha(theme.colors.primary, 0.18)}` : "none",
        ...A.rise(frame, delay, 24, 30),
      }}
    >
      <div
        style={{
          display: "inline-flex",
          padding: "8px 24px",
          borderRadius: 999,
          background: good ? theme.colors.primary : alpha(theme.colors.textSecondary, 0.14),
          border: good ? "none" : `1.5px solid ${theme.colors.line}`,
          color: good ? theme.colors.onPrimary : theme.colors.textSecondary,
          fontSize: 26,
          fontWeight: 700,
        }}
      >
        {label}
      </div>
      <div style={{ marginTop: 30, display: "flex", flexDirection: "column", gap: 26 }}>
        {items.map((text, i) => (
          <div
            key={i}
            style={{ display: "flex", alignItems: "flex-start", gap: 16, ...A.popBullet(frame, delay + 8 + i * 6, 18, 36) }}
          >
            <div
              style={{
                fontSize: 30,
                fontWeight: 800,
                color: good ? theme.colors.success : theme.colors.danger,
                flexShrink: 0,
                lineHeight: 1.4,
              }}
            >
              {good ? "✓" : "✗"}
            </div>
            <div
              style={{
                fontSize: 30,
                lineHeight: 1.5,
                color: good ? theme.colors.text : theme.colors.textSecondary,
              }}
            >
              {text}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

/** 10. 对比页：左右两栏 + VS 标识。 */
export const ComparisonPage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  return (
    <Page theme={theme}>
      <Header theme={theme} kicker={c.kicker ?? "COMPARISON · 对比"} title={c.title ?? ""}  transitionsOn={transitionsOn} />
<BodyRegion theme={theme}>
        <div style={{ position: "relative", display: "flex", gap: 96 }}>
          <CompareCard
            theme={theme}
            frame={frame}
            label={c.leftLabel ?? "对比 A"}
            items={c.left ?? []}
            good={false}
            transitionsOn={!!transitionsOn}
            delay={8}
          />
          <CompareCard
            theme={theme}
            frame={frame}
            label={c.rightLabel ?? "对比 B"}
            items={c.right ?? []}
            good={true}
            transitionsOn={!!transitionsOn}
            delay={14}
          />
          {/* VS 标识 */}
          <div
            style={{
              position: "absolute",
              left: "50%",
              top: "50%",
              transform: `translate(-50%, -50%) scale(${
                transitionsOn ? 1 : 0.6 + 0.4 * Math.min(1, Math.max(0, (frame - 20) / 18))
              })`,
              width: 100,
              height: 100,
              borderRadius: "50%",
              background: theme.colors.accent,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: theme.colors.onAccent,
              fontSize: 40,
              fontWeight: 900,
              boxShadow: `0 8px 40px ${alpha(theme.colors.accent, 0.45)}`,
              ...A.fade(frame, 20, 14),
            }}
          >
            VS
          </div>
        </div>
</BodyRegion>


      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
