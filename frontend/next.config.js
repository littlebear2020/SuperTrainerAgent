/**
 * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation. This is especially useful
 * for Docker builds.
 */
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";

import "./src/env.js";

// DSN-008（2026-09-14）：编译期注入版本/构建号（About 区显示）。
// git 不可用（如 CI 裸 tar 构建）时回退 "dev"，不阻断构建。
function getAppVersion() {
  try {
    const pkg = JSON.parse(
      readFileSync(new URL("./package.json", import.meta.url), "utf-8"),
    );
    return typeof pkg.version === "string" && pkg.version.length > 0
      ? pkg.version
      : "dev";
  } catch {
    return "dev";
  }
}

function getBuildId() {
  try {
    return (
      execSync("git rev-parse --short HEAD", {
        stdio: ["ignore", "pipe", "ignore"],
      })
        .toString()
        .trim() || "dev"
    );
  } catch {
    return "dev";
  }
}

function getInternalServiceURL(envKey, fallbackURL) {
  const configured = process.env[envKey]?.trim();
  return configured && configured.length > 0
    ? configured.replace(/\/+$/, "")
    : fallbackURL;
}
/** @type {import("next").NextConfig} */
const config = {
  // SEC-D1（批次 2 项 2.14）：i18n 块随模板 docs 站点一并移除——App Router
  // 本就不支持该配置（build 警告），且它让 Next 生成 /en/404 之类 locale
  // 页并要求 Pages Router 的 /_document（删除模板后 prerender 直接失败）。
  devIndicators: false,
  env: {
    // DSN-008：About 区「版本 x.y.z · 构建 abc123」行（env 缺失则不渲染该行）。
    // 构建号优先取注入 env（CI/Docker build-arg——容器内无 .git，git 回退只对
    // 本机 `next build` 生效）。
    NEXT_PUBLIC_APP_VERSION:
      process.env.NEXT_PUBLIC_APP_VERSION || getAppVersion(),
    NEXT_PUBLIC_BUILD_ID: process.env.NEXT_PUBLIC_BUILD_ID || getBuildId(),
  },
  async rewrites() {
    const rewrites = [];
    const gatewayURL = getInternalServiceURL(
      "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL",
      "http://127.0.0.1:8001",
    );

    if (!process.env.NEXT_PUBLIC_LANGGRAPH_BASE_URL) {
      rewrites.push({
        source: "/api/langgraph",
        destination: `${gatewayURL}/api`,
      });
      rewrites.push({
        source: "/api/langgraph/:path*",
        destination: `${gatewayURL}/api/:path*`,
      });
    }

    if (!process.env.NEXT_PUBLIC_BACKEND_BASE_URL) {
      rewrites.push({
        source: "/api/agents",
        destination: `${gatewayURL}/api/agents`,
      });
      rewrites.push({
        source: "/api/agents/:path*",
        destination: `${gatewayURL}/api/agents/:path*`,
      });
      rewrites.push({
        source: "/api/skills",
        destination: `${gatewayURL}/api/skills`,
      });
      rewrites.push({
        source: "/api/skills/:path*",
        destination: `${gatewayURL}/api/skills/:path*`,
      });

      // Catch-all for remaining gateway API routes (models, threads, memory,
      // mcp, artifacts, uploads, suggestions, runs, etc.) that don't have
      // their own NEXT_PUBLIC_* env var toggle.
      //
      // NOTE: this must come AFTER the /api/langgraph rewrite above so that
      // LangGraph-compatible routes keep their public prefix while Gateway
      // receives its native /api/* paths.
      rewrites.push({
        source: "/api/:path*",
        destination: `${gatewayURL}/api/:path*`,
      });
    }

    return rewrites;
  },
  // C9（2026-10-05 审计 §5-6）：安全响应头基线。CSP 先以 Report-Only 观察
  // （兼容现有 blob:/srcDoc/沙箱 iframe 用法需冒烟核实，R-4），一周无报后转 enforce。
  async headers() {
    const cspReportOnly = [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob: https:",
      "media-src 'self' blob: data:",
      "font-src 'self' data:",
      "connect-src 'self' http://127.0.0.1:8001 ws: wss:",
      "worker-src 'self' blob:",
      "frame-src 'self' blob:",
      "object-src 'none'",
      "base-uri 'self'",
    ].join("; ");
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          // SAMEORIGIN（非 DENY）：artifact/设计预览依赖同源 iframe
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Content-Security-Policy-Report-Only", value: cspReportOnly },
        ],
      },
    ];
  },
};

// SEC-D1（批次 2 项 2.14）：nextra/MDX 模板站点已删除——纯 Next 配置导出。
export default config;
