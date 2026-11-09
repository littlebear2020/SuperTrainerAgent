#!/usr/bin/env node
// 路由资产预算测量（对标上游 DeerFlow scripts/measure-route-assets.mjs，裁掉
// 双 build / 静态 demo 模式）：build → next start（临时端口）→ 按路由 fetch HTML
// → 提取 /_next/static/ 引用 → stat 累加字节 → 与 performance-budgets.json 比对。
// 上游此门禁未进 CI，本地接上即超上游（2026-10-16 深研 #9）。

import { spawn } from "node:child_process";
import { readFile, stat, writeFile } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

// 占位 thread id：chats/[id] 是同一 page bundle，资产集合与具体 id 无关。
const PLACEHOLDER_THREAD_ID = "00000000-0000-0000-0000-000000000000";
export const ROUTES = [
  "/",
  "/workspace/chats",
  `/workspace/chats/${PLACEHOLDER_THREAD_ID}`,
  "/workspace/video-gen",
  "/workspace/writer",
];

/**
 * build/start 环境固定注入免登录 + 跳过 env 校验（与 e2e webServer 同款）。
 * AUTH_DISABLED 下 /login 恒 307，路由表刻意不含 /login（PR-1 2.1）。
 * 参数放宽为 Record：process.env 可传入，测试也无须伪造完整 ProcessEnv
 * （NODE_ENV 等必填键是 Next 类型层要求，与测量逻辑无关）。
 *
 * @param {Record<string, string | undefined>} [baseEnvironment]
 */
export function createBuildEnvironment(baseEnvironment = process.env) {
  return {
    ...baseEnvironment,
    SKIP_ENV_VALIDATION: "1",
    DEER_FLOW_AUTH_DISABLED: "1",
  };
}

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(scriptDir, "..");

export function extractAssetPaths(html) {
  const assets = { css: [], js: [] };
  const seen = { css: new Set(), js: new Set() };
  const attributePattern = /(?:src|href)=["']([^"']+)["']/g;
  for (const match of html.matchAll(attributePattern)) {
    const value = match[1];
    if (!value?.startsWith("/_next/static/")) continue;
    const relativePath = value.slice("/_next/static/".length).split("?", 1)[0];
    if (!relativePath) continue;
    const kind = relativePath.endsWith(".css")
      ? "css"
      : relativePath.endsWith(".js")
        ? "js"
        : null;
    if (kind && !seen[kind].has(relativePath)) {
      seen[kind].add(relativePath);
      assets[kind].push(relativePath);
    }
  }
  return assets;
}

export function evaluateBudgets(measurements, budgets) {
  const failures = [];
  for (const [route, limits] of Object.entries(budgets)) {
    const actual = measurements[route];
    if (!actual) {
      failures.push(`${route}: route was not measured`);
      continue;
    }
    for (const kind of ["css", "js"]) {
      if (actual[kind] <= limits[kind]) continue;
      const overage = actual[kind] - limits[kind];
      failures.push(
        `${route} ${kind}: ${actual[kind]} bytes exceeds ${limits[kind]} bytes by ${overage} ${overage === 1 ? "byte" : "bytes"}`,
      );
    }
  }
  return failures;
}

export function formatMeasurementSummary(measurements) {
  const lines = ["Route asset summary"];
  for (const [route, measurement] of Object.entries(measurements)) {
    lines.push(
      `${route} JS ${measurement.js} B | CSS ${measurement.css} B | HTML ${measurement.html} B`,
    );
  }
  return lines.join("\n");
}

async function getFreePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Failed to allocate a local port"));
        return;
      }
      const { port } = address;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: frontendDir,
      env: createBuildEnvironment(),
      stdio: "inherit",
      ...options,
    });
    child.on("error", reject);
    child.on("exit", (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} exited with ${code ?? signal}`));
    });
  });
}

async function waitForServer(baseUrl, child) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Next.js server exited with ${child.exitCode}`);
    }
    try {
      const response = await fetch(baseUrl, { redirect: "manual" });
      if (response.status < 500) return;
    } catch {
      // The server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Timed out waiting for the Next.js production server");
}

async function measureRoute(baseUrl, route) {
  const response = await fetch(`${baseUrl}${route}`, { redirect: "manual" });
  if (!response.ok) {
    throw new Error(`${route} returned HTTP ${response.status}`);
  }
  const html = await response.text();
  const assets = extractAssetPaths(html);
  const totals = { css: 0, js: 0 };
  for (const kind of ["css", "js"]) {
    for (const relativePath of assets[kind]) {
      const assetPath = path.join(frontendDir, ".next", "static", relativePath);
      totals[kind] += (await stat(assetPath)).size;
    }
  }
  return { ...totals, assets, html: Buffer.byteLength(html) };
}

async function measureBuild(routes) {
  const env = createBuildEnvironment();
  await run("pnpm", ["exec", "next", "build"], { env });

  const port = await getFreePort();
  const server = spawn(
    "pnpm",
    [
      "exec",
      "next",
      "start",
      "--hostname",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    {
      cwd: frontendDir,
      env,
      stdio: ["ignore", "inherit", "inherit"],
    },
  );

  try {
    const baseUrl = `http://127.0.0.1:${port}`;
    await waitForServer(baseUrl, server);
    const measurements = {};
    for (const route of routes) {
      measurements[route] = await measureRoute(baseUrl, route);
    }
    return measurements;
  } finally {
    if (server.exitCode === null) {
      await new Promise((resolve) => {
        const forceStop = setTimeout(() => server.kill("SIGKILL"), 5_000);
        server.once("exit", () => {
          clearTimeout(forceStop);
          resolve();
        });
        server.kill("SIGTERM");
      });
    }
  }
}

async function main() {
  const check = process.argv.includes("--check");
  const measurements = await measureBuild(ROUTES);
  await writeFile(
    path.join(frontendDir, ".next", "performance-results.json"),
    `${JSON.stringify(measurements, null, 2)}\n`,
  );
  process.stdout.write(`${formatMeasurementSummary(measurements)}\n`);

  if (check) {
    const budgets = JSON.parse(
      await readFile(
        path.join(frontendDir, "performance-budgets.json"),
        "utf8",
      ),
    );
    const failures = evaluateBudgets(measurements, budgets);
    if (failures.length > 0) {
      throw new Error(`Route asset budgets failed:\n${failures.join("\n")}`);
    }
  }
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main().catch((error) => {
    process.stderr.write(
      `${error instanceof Error ? error.stack : String(error)}\n`,
    );
    process.exitCode = 1;
  });
}
