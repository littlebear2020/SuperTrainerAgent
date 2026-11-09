"use client";

import { Streamdown } from "streamdown";

import { aboutMarkdown } from "./about-content";

export function AboutSettingsPage() {
  // DSN-008（2026-09-14）：版本/构建号由 next.config.js 编译期注入
  // （NEXT_PUBLIC_APP_VERSION / NEXT_PUBLIC_BUILD_ID）；env 缺失（旧构建）
  // 则不渲染该行——介绍内容与版本信息分离（about-content.ts 不动）。
  const version = process.env.NEXT_PUBLIC_APP_VERSION;
  const buildId = process.env.NEXT_PUBLIC_BUILD_ID;
  return (
    <div className="space-y-4">
      <Streamdown>{aboutMarkdown}</Streamdown>
      {version && (
        <p className="text-muted-foreground text-xs">
          版本 {version}
          {buildId ? ` · 构建 ${buildId}` : ""}
        </p>
      )}
    </div>
  );
}
