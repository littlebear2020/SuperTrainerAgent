import React from "react";
import { Theme } from "../theme";

/**
 * T-D1（D3✅，2026-09-09 feedback-round1）：文字卡正文区——自画面纵向 38% 起。
 *
 * F2 根修：修前正文区紧跟标题顶排（内容少时整页挤在左上角，下方大片留白）。
 * 标题留左上（Header 原位），正文整体下移到画面 38%（1080×0.38≈410px）起。
 * 8 个文字卡版式（Concept/Objectives/Summary/Case/Comparison/Process/Chart/Code）
 * 统一经本组件布局；图文页（ImageText*）与全幅视觉页不动。
 *
 * 绝对定位（top 固定 410）而非 marginTop——Header 高度随 titleSize 变化，
 * 流式 margin 无法钉住"画面 38%"这一绝对位置。底部贴 Page 底部安全区（字幕 160）。
 */
export const BODY_TOP_PX = 410; // 1080 × 0.38 ≈ 410（D3 拍板 38%）

export const BodyRegion: React.FC<{ theme: Theme; children: React.ReactNode }> = ({ theme, children }) => (
  <div
    style={{
      position: "absolute",
      top: BODY_TOP_PX,
      left: theme.safe.side,
      right: theme.safe.side,
      bottom: theme.safe.bottom,
      display: "flex",
      flexDirection: "column",
    }}
  >
    {children}
  </div>
);
