import React from "react";
import { useCurrentFrame } from "remotion";
import { THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { Header } from "../components/Header";
import { BodyRegion } from "../components/BodyRegion";
import { SubtitleBar } from "../components/SubtitleBar";

const CHART_H = 440;

/** 11. 数据图表页：纯 div 柱状图依次升起 + 结论文字。 */
export const ChartPage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const data = c.chart ?? [];
  return (
    <Page theme={theme}>
      <Header theme={theme} kicker={c.kicker ?? "DATA · 数据说话"} title={c.title ?? ""}  transitionsOn={transitionsOn} />
<BodyRegion theme={theme}>
        <div style={{ display: "flex", gap: 56, flex: 1 }}>
          {/* 图表卡 */}
          <div
            style={{
              flex: 1,
              borderRadius: 24,
              backgroundColor: theme.colors.card,
              border: `1px solid ${theme.colors.line}`,
              padding: "44px 48px 0",
              ...A.rise(frame, 6, 24, 30),
            }}
          >
            <div
              style={{
                height: CHART_H,
                display: "flex",
                alignItems: "flex-end",
                justifyContent: "space-around",
                borderBottom: `2px solid ${theme.colors.line}`,
              }}
            >
              {data.map((d, i) => {
                const p = A.progress(frame, 10 + i * 6, 22);
                const last = i === data.length - 1;
                return (
                  <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
                    <div
                      style={{
                        fontSize: 30,
                        fontWeight: 800,
                        color: last ? theme.colors.accent : theme.colors.text,
                        opacity: p,
                      }}
                    >
                      {d.value}%
                    </div>
                    <div
                      style={{
                        width: 108,
                        height: (d.value / 100) * (CHART_H - 90),
                        borderRadius: "12px 12px 0 0",
                        background: last
                          ? `linear-gradient(180deg, ${theme.colors.accent}, ${theme.colors.accentDeep})`
                          : `linear-gradient(180deg, ${theme.colors.primary}, ${theme.colors.primaryDeep})`,
                        transform: `scaleY(${p})`,
                        transformOrigin: "bottom",
                      }}
                    />
                  </div>
                );
              })}
            </div>
            <div style={{ display: "flex", justifyContent: "space-around", padding: "16px 0 20px" }}>
              {data.map((d, i) => (
                <div
                  key={i}
                  style={{
                    width: 108,
                    textAlign: "center",
                    fontSize: 24,
                    color: theme.colors.textSecondary,
                    ...A.fade(frame, 12 + i * 6),
                  }}
                >
                  {d.label}
                </div>
              ))}
            </div>
          </div>
          {/* 结论栏 */}
          <div style={{ width: 520, display: "flex", flexDirection: "column", justifyContent: "center" }}>
            {c.headline ? (
              <div style={{ fontSize: 60, fontWeight: 900, color: theme.colors.accent, ...A.rise(frame, 22) }}>
                {c.headline}
              </div>
            ) : null}
            <div
              style={{
                marginTop: 24,
                fontSize: 30,
                lineHeight: 1.6,
                color: theme.colors.text,
                ...A.rise(frame, 26),
              }}
            >
              {c.conclusion ?? ""}
            </div>
            {c.note ? (
              <div style={{ marginTop: 20, fontSize: 22, color: theme.colors.textDim, ...A.rise(frame, 30) }}>
                {c.note}
              </div>
            ) : null}
          </div>
        </div>
</BodyRegion>


      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
