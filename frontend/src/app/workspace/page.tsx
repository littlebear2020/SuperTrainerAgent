"use client";

import dynamic from "next/dynamic";

// 2026-09-22 工作台恢复：撤销 09-07 的 307 重定向（3e44f5389 反向），
// /workspace 直接渲染六能力网格首页（hero + 固定能力卡片）。
const WorkspaceHome = dynamic(
  () => import("@/components/workspace/workspace-home"),
  { ssr: false },
);

export default function WorkspacePage() {
  return <WorkspaceHome />;
}
