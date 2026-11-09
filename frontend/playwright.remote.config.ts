/**
 * T-Z 浏览器手测配置（2026-09-09 feedback-round1）：
 * 直接打测试服务器（真实 gateway + 真实前端构建 + 真实登录），无 mock、无本地 webServer。
 * 用法：E2E_BASE=http://localhost:2026 npx playwright test -c playwright.remote.config.ts
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e-remote",
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE ?? "http://localhost:2026",
    trace: "off",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
