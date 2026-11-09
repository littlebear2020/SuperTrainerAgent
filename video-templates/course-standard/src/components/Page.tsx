import React from "react";
import { AbsoluteFill } from "remotion";
import { Theme } from "../theme";
import { Backdrop } from "./Backdrop";

/**
 * 页面外壳：统一背景 + 安全区边距。
 * 默认左右 60px、上下 64px/160px（底部为字幕预留区）。
 * noPadding 用于全幅视觉类版式。
 */
export const Page: React.FC<{
  theme: Theme;
  children: React.ReactNode;
  noPadding?: boolean;
}> = ({ theme, children, noPadding }) => {
  return (
    <AbsoluteFill style={{ fontFamily: theme.fontFamily, backgroundColor: theme.colors.bg }}>
      <Backdrop theme={theme} />
      <AbsoluteFill
        style={
          noPadding
            ? undefined
            : {
                padding: `64px ${theme.safe.side}px ${theme.safe.bottom}px`,
              }
        }
      >
        {children}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
