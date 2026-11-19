/**
 * live E2E 的目标地址解析——playwright.live.config.ts 与 global-setup.ts 共用，
 * 避免校验逻辑与报错文案在两处各写一份后漂移。
 *
 * 地址一律经环境变量注入，禁止把真实主机写进仓库（AGENTS.md「服务器与部署」）。
 */

/**
 * 读取被测部署的根地址（含协议与端口）。
 * 缺失时抛出带明确指引的错误，而不是静默回退到某个硬编码默认值——
 * 后者会让"忘记设置"变成"打到了错误的服务器"。
 */
export function getLiveBaseUrl(): string {
  const baseURL = process.env.LIVE_BASE_URL;
  if (!baseURL) {
    throw new Error(
      "缺少 LIVE_BASE_URL:请设置为被测部署的根地址(含协议与端口),不写入仓库",
    );
  }
  return baseURL;
}
