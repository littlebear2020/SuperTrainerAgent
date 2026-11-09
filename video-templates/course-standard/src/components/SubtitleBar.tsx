import React from "react";
import { useCurrentFrame } from "remotion";
import { Theme } from "../theme";
import { rise } from "../lib/anim";

/**
 * 公共字幕条：位于底部 160px 字幕安全区内，字号 36px。
 * 四主题统一深底浅字（浅色主题下依然是深底条，保证可读性）。
 */
export const SubtitleBar: React.FC<{ theme: Theme; text: string; delay?: number }> = ({
  theme,
  text,
  delay = 20,
}) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        position: "absolute",
        left: theme.safe.side,
        right: theme.safe.side,
        bottom: 52,
        display: "flex",
        justifyContent: "center",
        fontFamily: theme.fontFamily,
        ...rise(frame, delay, 18, 24),
      }}
    >
      <div
        style={{
          maxWidth: 1520,
          padding: "16px 44px",
          borderRadius: 18,
          backgroundColor: theme.colors.subtitleBg,
          border: `1px solid ${theme.colors.subtitleBorder}`,
          color: theme.colors.subtitleText,
          fontSize: theme.type.subtitle,
          lineHeight: 1.4,
          textAlign: "center",
        }}
      >
        {text}
      </div>
    </div>
  );
};
