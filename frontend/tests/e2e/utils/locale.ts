import type { Page } from "@playwright/test";

/**
 * DSN-003b（2026-09-14）默认语言改中文后，历史 e2e 的英文断言（placeholder/
 * 按钮文案）在全新 context 的新访客默认 zh-CN 下失配。本 helper 在导航前
 * 注入 locale cookie——服务端 SSR 与客户端经同一 cookie 解析，两处同步生效。
 * 断言中文硬编码文案的用例（如 storyboard-confirm-card）不受影响：那些
 * 字串不走 i18n。
 */
// ⚠️ 与 playwright.config.ts 的 use.baseURL 耦合（review 2026-09-22 三-3：
// Playwright 1.59 的 BrowserContext 不暴露 baseURL() getter，无法从 page
// 派生，已核实类型定义）。改端口/换 host 跑 e2e 时必须两处同步，否则
// cookie 静默失效、断言批量红。
const E2E_BASE_URL = `http://localhost:${process.env.E2E_PORT ?? 3000}`;

export async function pinEnglishLocale(page: Page): Promise<void> {
  await page
    .context()
    .addCookies([{ name: "locale", value: "en-US", url: E2E_BASE_URL }]);
}
