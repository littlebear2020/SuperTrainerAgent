import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://192.0.2.10:2026",
}));

import { getVideoDownloadUrl } from "@/core/video-gen/api";
import { getProductionDownloadURL } from "@/core/voice-twin/production-api";

test("video download helper returns base exactly once (callers must NOT prepend)", () => {
  const url = getVideoDownloadUrl("vg_abc");
  expect(url).toBe("http://192.0.2.10:2026/api/video/tasks/vg_abc/download");
  expect(url.match(/192\.0\.2\.10:2026/g)).toHaveLength(1);
});

test("voice production download helper returns base exactly once", () => {
  const url = getProductionDownloadURL("task_abc");
  expect(url.match(/192\.0\.2\.10:2026/g)).toHaveLength(1);
  expect(url).toContain("/api/voice-twins/production/task_abc/download");
});

// 源码级守卫（无先例，heartbeat.test.ts 同款模式）：此 bug 的复发路径是调用方
// 再次拼接而非 helper 本身——锁定两个面板不得出现 base+helper 双拼模式。
test("writer panels never prepend getBackendBaseURL to a URL helper result", () => {
  for (const rel of ["src/components/workspace/writer/voice-panel.tsx"]) {
    const src = readFileSync(
      resolve(__dirname, "../../../../..", rel),
      "utf-8",
    );
    expect(src).not.toMatch(/getBackendBaseURL\(\)\}\$\{get/);
  }
});
