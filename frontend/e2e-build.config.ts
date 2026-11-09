import { defineConfig, devices } from "@playwright/test";

/**
 * E2E build config: 启动独立 build server (port 3001) 带 DEER_FLOW_AUTH_DISABLED,
 * 不复用 3000 dev server（dev server 没有该 env，SSR 认证会把未登录用户重定向到登录页，
 * 导致 E2E 假阳性）。本计划所有 E2E 用此 config 跑。
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: false,
  retries: 0,
  reporter: "line",
  timeout: 60_000,

  use: {
    baseURL: "http://localhost:3001",
    ...devices["Desktop Chrome"],
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: {
    command: "pnpm build && pnpm start",
    url: "http://localhost:3001",
    reuseExistingServer: false,
    timeout: 240_000,
    env: {
      SKIP_ENV_VALIDATION: "1",
      DEER_FLOW_AUTH_DISABLED: "1",
      PORT: "3001",
    },
  },
});
