import React from "react";
import { useCurrentFrame } from "remotion";
import { alpha, THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { Header } from "../components/Header";
import { SubtitleBar } from "../components/SubtitleBar";

/** 17. 思考题/互动页：问题 + 4 个选项占位。 */
export const QuizPage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const options = c.options ?? [];
  return (
    <Page theme={theme}>
      <Header theme={theme} kicker={c.kicker ?? "QUIZ · 思考题"} title={c.title ?? ""} transitionsOn={transitionsOn} />
      <div
        style={{
          marginTop: 40,
          padding: "30px 40px",
          borderRadius: 20,
          backgroundColor: alpha(theme.colors.accent, 0.08),
          border: `1.5px solid ${theme.colors.accent}`,
          fontSize: 38,
          fontWeight: 700,
          color: theme.colors.text,
          lineHeight: 1.5,
          ...A.rise(frame, 8),
        }}
      >
        {c.question ?? ""}
      </div>
      <div style={{ marginTop: 36, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
        {options.map((opt, i) => (
          <div
            key={opt.key}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 22,
              padding: "26px 30px",
              borderRadius: 20,
              backgroundColor: theme.colors.card,
              border: `1px solid ${theme.colors.line}`,
              ...A.rise(frame, 14 + i * 6, 20, 30),
            }}
          >
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: 14,
                border: `2px solid ${theme.colors.primary}`,
                color: theme.colors.primary,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 28,
                fontWeight: 800,
                flexShrink: 0,
              }}
            >
              {opt.key}
            </div>
            <div style={{ fontSize: 30, color: theme.colors.text, lineHeight: 1.45 }}>{opt.text}</div>
          </div>
        ))}
      </div>
      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
