import React from "react";
import { AbsoluteFill } from "remotion";
import { Theme } from "../theme";

/** 统一氛围背景：主题底色 + 两团低透明度品牌色光晕。 */
export const Backdrop: React.FC<{ theme: Theme }> = ({ theme }) => {
  return (
    <AbsoluteFill style={{ backgroundColor: theme.colors.bg, overflow: "hidden" }}>
      <div
        style={{
          position: "absolute",
          top: -260,
          right: -200,
          width: 720,
          height: 720,
          borderRadius: "50%",
          background: theme.colors.primary,
          opacity: theme.colors.glowPrimary,
          filter: "blur(140px)",
        }}
      />
      <div
        style={{
          position: "absolute",
          bottom: -300,
          left: -240,
          width: 640,
          height: 640,
          borderRadius: "50%",
          background: theme.colors.accent,
          opacity: theme.colors.glowAccent,
          filter: "blur(150px)",
        }}
      />
    </AbsoluteFill>
  );
};
