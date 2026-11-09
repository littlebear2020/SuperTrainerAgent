import React from "react";
import { useCurrentFrame } from "remotion";
import { alpha, THEMES } from "../theme";
import { animKit } from "../lib/anim";
import { LayoutSceneProps } from "../content";
import { Page } from "../components/Page";
import { SubtitleBar } from "../components/SubtitleBar";
import { VisualSlot } from "../components/VisualSlot";

/**
 * 9. 视频片段页：顶部标题条 + 16:9 视频区 + 底部字幕条。
 * 素材缺失回退播放键占位观感。
 */
export const VideoClipPage: React.FC<LayoutSceneProps> = ({ themeName, content, visualSpec, transitionsOn, subtitleText }) => {
  const theme = THEMES[themeName];
  const frame = useCurrentFrame();
  const A = animKit(!!transitionsOn);
  const c = content ?? {};
  return (
    <Page theme={theme}>
      {/* 顶部标题条 */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 18, ...A.rise(frame, 0) }}>
          <div style={{ width: 8, height: 44, borderRadius: 4, background: theme.colors.accent }} />
          <div style={{ fontSize: 44, fontWeight: 800, color: theme.colors.text }}>
            {c.title ?? ""}
          </div>
        </div>
        {c.badge ? (
          <div
            style={{
              padding: "8px 22px",
              borderRadius: 999,
              border: `1.5px solid ${theme.colors.line}`,
              color: theme.colors.textSecondary,
              fontSize: 24,
              ...A.fade(frame, 6),
            }}
          >
            {c.badge}
          </div>
        ) : null}
      </div>
      {/* 16:9 视频区：素材优先，缺省回退占位观感 */}
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        {visualSpec && visualSpec.path && visualSpec.kind === "video" ? (
          <div style={{ ...A.rise(frame, 6, 24, 28) }}>
            <VisualSlot theme={theme} spec={visualSpec} width={1280} height={720} />
          </div>
        ) : (
          <div
            style={{
              width: 1280,
              height: 720,
              borderRadius: 24,
              background: theme.colors.bgDeep,
              border: `2px solid ${theme.colors.line}`,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 22,
              ...A.rise(frame, 6, 24, 28),
            }}
          >
            <div
              style={{
                width: 110,
                height: 110,
                borderRadius: "50%",
                backgroundColor: alpha(theme.colors.primary, 0.18),
                border: `2px solid ${theme.colors.primary}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <div
                style={{
                  marginLeft: 10,
                  width: 0,
                  height: 0,
                  borderTop: "24px solid transparent",
                  borderBottom: "24px solid transparent",
                  borderLeft: `38px solid ${theme.colors.subtitleText}`,
                }}
              />
            </div>
            <div style={{ color: theme.colors.textSecondary, fontSize: 26 }}>
              视频占位 · 16:9（1280 × 720 渲染区）
            </div>
          </div>
        )}
      </div>
      {subtitleText ? <SubtitleBar theme={theme} text={subtitleText} /> : null}
    </Page>
  );
};
