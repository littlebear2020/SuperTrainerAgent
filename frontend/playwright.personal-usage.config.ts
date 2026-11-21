import { defineConfig } from "@playwright/test";

import baseConfig from "./playwright.config";

const port = Number(process.env.E2E_PORT ?? 3127);
const gatewayPort = Number(process.env.E2E_GATEWAY_PORT ?? 3137);
const gatewayURL = `http://127.0.0.1:${gatewayPort}`;
const appURL = `http://localhost:${port}`;

export default defineConfig({
  ...baseConfig,
  testMatch: "**/*.e2e.ts",
  fullyParallel: false,
  workers: 1,
  use: {
    ...baseConfig.use,
    baseURL: appURL,
  },
  webServer: [
    {
      command: "node tests/e2e/utils/download-gateway.mjs",
      url: `${gatewayURL}/health`,
      reuseExistingServer: false,
      timeout: 30_000,
      env: { E2E_GATEWAY_PORT: String(gatewayPort) },
    },
    {
      command: `pnpm build && pnpm exec next start --port ${port}`,
      url: appURL,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        SKIP_ENV_VALIDATION: "1",
        DEER_FLOW_AUTH_DISABLED: "0",
        DEER_FLOW_INTERNAL_GATEWAY_BASE_URL: gatewayURL,
        DEER_FLOW_TRUSTED_ORIGINS: `${appURL},http://127.0.0.1:${port}`,
      },
    },
  ],
});
