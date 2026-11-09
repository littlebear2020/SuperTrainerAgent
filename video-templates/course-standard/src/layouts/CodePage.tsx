import React from "react";
import { useCurrentFrame } from "remotion";
import { THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { Header } from "../components/Header";
import { BodyRegion } from "../components/BodyRegion";
import { SubtitleBar } from "../components/SubtitleBar";

/** 代码语法配色：bgDeep 在四个主题下均为深色，调色板通用。 */
const C = {
  comment: "#64748B",
  keyword: "#93C5FD",
  string: "#86EFAC",
  fn: "#F59E0B",
  plain: "#E2E8F0",
};

/** 轻量语法高亮：注释/字符串/关键字/函数名（近似 M0 手写配色）。 */
const TOKEN_RE = /("[^"\n]*")|([A-Za-z_][A-Za-z0-9_.]*)|(.)/g;

const classifyToken = (token: string): string => {
  if (token.startsWith('"')) return C.string;
  if (token === "print" || token.endsWith(".invoke")) return C.fn;
  if (token === "prompt" || token === "resp") return C.keyword;
  return C.plain;
};

const highlightLine = (line: string): React.ReactNode[] => {
  if (line.trimStart().startsWith("#")) {
    return [<span key="c" style={{ color: C.comment }}>{line}</span>];
  }
  const nodes: React.ReactNode[] = [];
  const matches = line.matchAll(TOKEN_RE);
  let i = 0;
  for (const m of matches) {
    const token = m[0];
    if (!token) continue;
    const color = classifyToken(token);
    nodes.push(
      color === C.plain ? token : <span key={i++} style={{ color }}>{token}</span>,
    );
  }
  return nodes.length ? nodes : [line];
};

/** 13. 代码讲解页：左侧代码块（深色底 + 简化语法色）+ 右侧要点说明。 */
export const CodePage: React.FC<LayoutSceneProps> = ({ themeName, content, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  const codeLines = (c.code ?? "").split("\n");
  return (
    <Page theme={theme}>
      <Header
        theme={theme}
        kicker={c.kicker ?? "CODE · 代码示例"}
        title={c.title ?? ""}
        titleSize={theme.type.sectionTitle}
        transitionsOn={transitionsOn}
      />
<BodyRegion theme={theme}>
        <div style={{ display: "flex", gap: 56, flex: 1 }}>
          {/* 代码块 */}
          <div
            style={{
              width: 1060,
              borderRadius: 20,
              backgroundColor: theme.colors.bgDeep,
              border: `1px solid ${theme.colors.line}`,
              overflow: "hidden",
              ...A.rise(frame, 6, 24, 30),
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "14px 22px",
                borderBottom: `1px solid ${theme.colors.line}`,
              }}
            >
              {["#F87171", "#FBBF24", "#34D399"].map((color) => (
                <div key={color} style={{ width: 16, height: 16, borderRadius: "50%", background: color }} />
              ))}
              <div style={{ marginLeft: 14, color: theme.colors.textDim, fontSize: 22, fontFamily: theme.monoFamily }}>
                {c.fileName ?? "code.py"}
              </div>
            </div>
            <pre
              style={{
                margin: 0,
                padding: "28px 34px",
                fontFamily: theme.monoFamily,
                fontSize: 25,
                lineHeight: 1.6,
                color: C.plain,
                whiteSpace: "pre-wrap",
              }}
            >
              {codeLines.map((line, i) => (
                <React.Fragment key={i}>
                  {i > 0 ? "\n" : ""}
                  {highlightLine(line)}
                </React.Fragment>
              ))}
            </pre>
          </div>
          {/* 右侧要点 */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: 34 }}>
            {(c.bullets ?? []).map((text, i) => (
              <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 18, ...A.popBullet(frame, 14 + i * 7, 20, 40) }}>
                <div
                  style={{
                    marginTop: 6,
                    width: 14,
                    height: 14,
                    borderRadius: 4,
                    background: theme.colors.accent,
                    transform: "rotate(45deg)",
                    flexShrink: 0,
                  }}
                />
                <div style={{ fontSize: 30, lineHeight: 1.55, color: theme.colors.text }}>{text}</div>
              </div>
            ))}
          </div>
        </div>
</BodyRegion>


      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
