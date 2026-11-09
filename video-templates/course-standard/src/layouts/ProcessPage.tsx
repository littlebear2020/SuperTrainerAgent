import React from "react";
import { useCurrentFrame } from "remotion";
import { alpha, THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { Header } from "../components/Header";
import { BodyRegion } from "../components/BodyRegion";
import { SubtitleBar } from "../components/SubtitleBar";

/** 12. 流程步骤页：横向 4 步流程条，箭头连接，当前步高亮。 */
export const ProcessPage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const steps = c.steps ?? [];
  const current = c.currentStep ?? -1;
  return (
    <Page theme={theme}>
      <Header theme={theme} kicker={c.kicker ?? "PROCESS · 流程"} title={c.title ?? ""} transitionsOn={transitionsOn} />
<BodyRegion theme={theme}>
        <div style={{ flex: 1, display: "flex", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "stretch", gap: 0, width: "100%" }}>
            {steps.map((step, i) => {
              const active = i === current;
              return (
                <React.Fragment key={i}>
                  {i > 0 && (
                    <div
                      style={{
                        alignSelf: "center",
                        padding: "0 18px",
                        fontSize: 44,
                        color: theme.colors.textDim,
                        ...A.fade(frame, 14 + i * 6),
                      }}
                    >
                      →
                    </div>
                  )}
                  <div
                    style={{
                      flex: 1,
                      borderRadius: 24,
                      padding: "36px 30px",
                      backgroundColor: active ? alpha(theme.colors.primary, 0.14) : theme.colors.card,
                      border: `2px solid ${active ? theme.colors.primary : theme.colors.line}`,
                      boxShadow: active ? `0 0 50px ${alpha(theme.colors.primary, 0.2)}` : "none",
                      ...A.rise(frame, 8 + i * 6, 22, 34),
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <div
                        style={{
                          fontSize: 46,
                          fontWeight: 900,
                          color: active ? theme.colors.accent : theme.colors.textDim,
                        }}
                      >
                        {String(i + 1).padStart(2, "0")}
                      </div>
                      {active && (
                        <div
                          style={{
                            padding: "4px 14px",
                            borderRadius: 999,
                            background: theme.colors.accent,
                            color: theme.colors.onAccent,
                            fontSize: 20,
                            fontWeight: 800,
                          }}
                        >
                          当前步骤
                        </div>
                      )}
                    </div>
                    <div
                      style={{
                        fontSize: 38,
                        fontWeight: 800,
                        color: active ? theme.colors.text : theme.colors.textSecondary,
                      }}
                    >
                      {step.name}
                    </div>
                    <div style={{ marginTop: 12, fontSize: 25, lineHeight: 1.5, color: theme.colors.textSecondary }}>
                      {step.desc}
                    </div>
                  </div>
                </React.Fragment>
              );
            })}
          </div>
        </div>
</BodyRegion>


      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
