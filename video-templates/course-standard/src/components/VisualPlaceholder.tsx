import React from "react";
import { alpha, Theme } from "../theme";

/**
 * 素材占位块：主题色渐变 + 虚线描边 + 图标文字「AI 素材位」。
 * 不使用任何外链图片，渲染自包含；浅色主题下自动换浅底深字。
 */
export const VisualPlaceholder: React.FC<{
  theme: Theme;
  width: number | string;
  height: number | string;
  label?: string;
  note?: string;
  radius?: number;
  iconSize?: number;
  style?: React.CSSProperties;
}> = ({
  theme,
  width,
  height,
  label = "AI 素材位",
  note,
  radius = 24,
  iconSize = 72,
  style,
}) => {
  return (
    <div
      style={{
        width,
        height,
        borderRadius: radius,
        background: `linear-gradient(135deg, ${theme.colors.visualFrom} 0%, ${theme.colors.visualVia} 55%, ${theme.colors.visualTo} 140%)`,
        border: `2px dashed ${alpha(theme.colors.textSecondary, 0.5)}`,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        fontFamily: theme.fontFamily,
        ...style,
      }}
    >
      <svg
        width={iconSize}
        height={iconSize}
        viewBox="0 0 24 24"
        fill="none"
        stroke={theme.colors.textSecondary}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="3" y="3" width="18" height="18" rx="3" />
        <circle cx="9" cy="9" r="2" />
        <path d="M21 15l-4.5-4.5L6 21" />
      </svg>
      <div style={{ color: theme.colors.text, fontSize: 30, fontWeight: 700 }}>{label}</div>
      {note ? (
        <div style={{ color: theme.colors.textSecondary, fontSize: 22 }}>{note}</div>
      ) : null}
    </div>
  );
};
