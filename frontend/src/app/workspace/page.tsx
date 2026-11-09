"use client";

import dynamic from "next/dynamic";

// 2026-09-22 工作台恢复：撤销 09-07 的 307 重定向（3e44f5389 反向），
// /workspace 直接渲染九宫格工作台首页（hero + 专家 Agent 网格 + 近期项目）。
const WorkspaceHome = dynamic(
  () => import("@/components/workspace/workspace-home"),
  { ssr: false },
);

export default function WorkspacePage() {
  return <WorkspaceHome />;
}
