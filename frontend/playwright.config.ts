import { defineConfig, devices } from "@playwright/test";

// 双 lane 用 E2E_PORT / E2E_GATEWAY_PORT 错开；两个服务均由 Playwright 独占启动。
const port = Number(process.env.E2E_PORT ?? 3107);
const gatewayPort = Number(process.env.E2E_GATEWAY_PORT ?? port + 10);
const gatewayURL = `http://127.0.0.1:${gatewayPort}`;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "html",
  timeout: 30_000,

  use: {
    baseURL: `http://localhost:${port}`,
    trace: "on-first-retry",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: [
    {
      command: "node tests/e2e/utils/download-gateway.mjs",
      url: `${gatewayURL}/health`,
      reuseExistingServer: false,
      timeout: 30_000,
      env: { E2E_GATEWAY_PORT: String(gatewayPort) },
    },
    {
      // pnpm exec 直连 next：pnpm run 转发 `--` 分隔符不剥离，`pnpm start --
      // --port N` 会让 next 把 --port 当目录参数（Invalid project directory）。
      command: `pnpm build && pnpm exec next start --port ${port}`,
      url: `http://localhost:${port}`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        SKIP_ENV_VALIDATION: "1",
        DEER_FLOW_AUTH_DISABLED: "1",
        DEER_FLOW_INTERNAL_GATEWAY_BASE_URL: gatewayURL,
      },
    },
  ],
});
