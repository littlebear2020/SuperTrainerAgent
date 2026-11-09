/**
 * DSN-003b（2026-09-14 拍板 #6）+ review-②：语言决议链静态守卫。
 *
 * 拍板验收「无痕窗口首访全站中文」——无 cookie 的新访客必须落 DEFAULT_LOCALE
 * (zh-CN)，不得回退成 detectLocale()（navigator.language）优先：英文浏览器
 * 新访客会 SSR 首帧中文、水合后翻英文并落 cookie，违背拍板语义。
 * detectLocale 本体保留（datetime.ts 日期格式化仍用），只是不再是决议链一环。
 */
import { readFileSync } from "fs";
import { join } from "path";

import { expect, test } from "vitest";

import { DEFAULT_LOCALE } from "@/core/i18n";

const hooksSrc = readFileSync(
  join(process.cwd(), "src/core/i18n/hooks.ts"),
  "utf-8",
);

test("DEFAULT_LOCALE 为 zh-CN（拍板 #6）", () => {
  expect(DEFAULT_LOCALE).toBe("zh-CN");
});

test("无 cookie 分支落 DEFAULT_LOCALE，不回退浏览器语言检测（review-②）", () => {
  // 检调用/导入形态（含括号）——注释里的提及不算回退。\s* 容许格式化产生的
  // 括号前空白（detectLocale (…)），防止守卫被空白绕过。
  expect(hooksSrc).not.toMatch(/detectLocale\s*\(/);
  expect(hooksSrc).toContain("setLocaleInCookie(DEFAULT_LOCALE)");
});
