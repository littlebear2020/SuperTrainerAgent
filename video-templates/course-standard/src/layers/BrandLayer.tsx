import React from "react";
import { Img, staticFile } from "remotion";
import { Theme } from "../theme";

/**
 * 品牌盖章层（内容场景）：品牌 LOGO 水印，位于字幕安全区上方的右下角
 * （居中字幕条 maxWidth 1520，右下角落空闲）。无 LOGO 时不渲染（保持 M0 观感）。
 */
export const BrandLayer: React.FC<{ theme: Theme; logoPath?: string }> = ({ theme, logoPath }) => {
  if (!logoPath) return null;
  const src = logoPath.startsWith("http") ? logoPath : staticFile(logoPath);
  return (
    <div
      style={{
        position: "absolute",
        right: 36,
        bottom: theme.safe.bottom - 76,
        opacity: 0.85,
        pointerEvents: "none",
      }}
    >
      <Img src={src} style={{ height: 56, objectFit: "contain" }} />
    </div>
  );
};
