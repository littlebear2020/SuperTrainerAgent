import React from "react";
import { useCurrentFrame } from "remotion";
import { alpha, THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { Header } from "../components/Header";
import { SubtitleBar } from "../components/SubtitleBar";

/** 3. 课程大纲页：章节列表 + 当前章高亮。 */
export const OutlinePage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const chapters = c.chapters ?? [];
  const current = c.currentChapter ?? -1;
  return (
    <Page theme={theme}>
      <Header theme={theme} kicker={c.kicker ?? "OUTLINE · 课程大纲"} title={c.title ?? ""}  transitionsOn={transitionsOn} />
      <div style={{ marginTop: 44, display: "flex", flexDirection: "column", gap: 16 }}>
        {chapters.map((title, i) => {
          const active = i === current;
          return (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 24,
                padding: "16px 28px",
                borderRadius: 18,
                border: `2px solid ${active ? theme.colors.primary : "transparent"}`,
                backgroundColor: active ? alpha(theme.colors.primary, 0.12) : theme.colors.card,
                ...A.popBullet(frame, 6 + i * 5),
              }}
            >
              <div
                style={{
                  width: 60,
                  height: 60,
                  borderRadius: 16,
                  background: active ? theme.colors.primary : "transparent",
                  border: `2px solid ${active ? theme.colors.primary : theme.colors.line}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: active ? theme.colors.onPrimary : theme.colors.textSecondary,
                  fontSize: 26,
                  fontWeight: 800,
                  flexShrink: 0,
                }}
              >
                {String(i + 1).padStart(2, "0")}
              </div>
              <div
                style={{
                  flex: 1,
                  fontSize: 34,
                  fontWeight: active ? 700 : 500,
                  color: active ? theme.colors.text : theme.colors.textSecondary,
                }}
              >
                {title}
              </div>
              {active && (
                <div
                  style={{
                    padding: "6px 18px",
                    borderRadius: 999,
                    background: theme.colors.accent,
                    color: theme.colors.onAccent,
                    fontSize: 22,
                    fontWeight: 800,
                  }}
                >
                  进行中
                </div>
              )}
            </div>
          );
        })}
      </div>
      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
