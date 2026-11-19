import { defineConfig, devices } from "@playwright/test";

import { getLiveBaseUrl } from "./tests/e2e/live/config";

/**
 * Live 服务器 E2E 套件配置——打真实部署,真实账号、真实模型链路;
 * 与本地 mock 套件(playwright.config.ts)互不干扰:
 *  - 本配置 testDir 仅收 tests/e2e/live/,且不起 webServer(服务已在线);
 *  - 主配置已 testIgnore live/ 目录,常规 `pnpm test:e2e` 不会误跑。
 *
 * 凭据与目标地址一律经环境变量注入,禁止写入仓库:
 *  LIVE_BASE_URL(必填) / LIVE_EMAIL / LIVE_TEST_PASSWORD。
 *
 * baseURL 用 getter 惰性求值:静态分析工具(knip)会 import 本文件,
 * 若在模块顶层的 defineConfig 里直接调用 getLiveBaseUrl(),缺少环境变量时会
 * 让 `pnpm check:dead` 直接失败。真正跑测试时 Playwright 读取 use.baseURL
 * 才触发校验,缺 LIVE_BASE_URL 依旧立即报错。
 */
export default defineConfig({
  testDir: "./tests/e2e/live",
  globalSetup: "./tests/e2e/live/global-setup",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: "test-results/live-html", open: "never" }],
  ],
  timeout: 60_000,
  outputDir: "test-results/live",
  use: {
    get baseURL() {
      return getLiveBaseUrl();
    },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    locale: "zh-CN",
    storageState: "tests/e2e/live/.auth/state.json",
  },
  projects: [
    {
      name: "chromium-live",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
