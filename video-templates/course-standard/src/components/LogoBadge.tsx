import React from "react";
import { Img, staticFile } from "remotion";
import { Theme } from "../theme";

/** 品牌 LOGO 徽标：logoUrl 提供时渲染图片，否则回退 M0 渐变占位块。 */
export const LogoBadge: React.FC<{ theme: Theme; size?: number; logoUrl?: string }> = ({
  theme,
  size = 56,
  logoUrl,
}) => {
  if (logoUrl) {
    const src = logoUrl.startsWith("http") ? logoUrl : staticFile(logoUrl);
    return (
      <Img
        src={src}
        style={{
          width: size,
          height: size,
          borderRadius: Math.round(size * 0.26),
          objectFit: "contain",
          flexShrink: 0,
        }}
      />
    );
  }
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: Math.round(size * 0.26),
        background: `linear-gradient(135deg, ${theme.colors.primary}, ${theme.colors.accent})`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: theme.colors.onPrimary,
        fontWeight: 800,
        fontSize: Math.round(size * 0.24),
        letterSpacing: 1,
        fontFamily: theme.fontFamily,
        flexShrink: 0,
      }}
    >
      LOGO
    </div>
  );
};
