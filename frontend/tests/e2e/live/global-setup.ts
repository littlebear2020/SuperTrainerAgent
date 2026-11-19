import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { request } from "@playwright/test";

/**
 * Live 套件 global setup:API 登录一次,storageState 落盘供全部用例复用。
 * 凭据从环境变量读取(LIVE_EMAIL / LIVE_TEST_PASSWORD),失败即中止整轮。
 */
const STATE_PATH = path.join(
  process.cwd(),
  "tests",
  "e2e",
  "live",
  ".auth",
  "state.json",
);

import { getLiveBaseUrl } from "./config";

async function globalSetup() {
  const baseURL = getLiveBaseUrl();
  const email = process.env.LIVE_EMAIL;
  const password = process.env.LIVE_TEST_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "缺少凭据:请设置 LIVE_EMAIL 与 LIVE_TEST_PASSWORD 环境变量(见仓库外 ops 记录)",
    );
  }

  const ctx = await request.newContext({ baseURL });
  // OAuth2PasswordRequestForm:username + password,urlencoded 表单(username 即邮箱)。
  const resp = await ctx.post("/api/v1/auth/login/local", {
    form: { username: email, password },
  });
  if (!resp.ok()) {
    const body = await resp.text().catch(() => "");
    throw new Error(
      `API 登录失败 ${resp.status()}: ${body.slice(0, 300)}(检查账号密码与服务器状态)`,
    );
  }
  const state = await ctx.storageState();
  if (!state.cookies.length) {
    throw new Error(
      "登录成功但未捕获到会话 cookie——live 套件依赖 cookie storageState,请检查鉴权实现",
    );
  }
  await mkdir(path.dirname(STATE_PATH), { recursive: true });
  await writeFile(STATE_PATH, JSON.stringify(state, null, 2));
  await ctx.dispose();
  console.log(
    `[live] logged in as ${email}, state saved (${state.cookies.length} cookies)`,
  );
}

export default globalSetup;
