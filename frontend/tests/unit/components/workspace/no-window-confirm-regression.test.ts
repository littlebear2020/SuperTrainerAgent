/**
 * 静态守卫（OBS-3 收尾，2026-10-09）：业务代码零 `window.confirm(` 回潮。
 *
 * 弹层统一批把 6 文件 11 处 window.confirm 全部换成 useConfirm 弹层后，
 * 防止新代码再引入原生 confirm（阻塞主线程、样式割裂、不可测试）。
 * 例外：components/ui/、ai-elements/（registry 生成物禁区）；注释中的
 * 提及（如 dialog-provider 的 jsdoc）不算命中。
 */

import { readFileSync, readdirSync, statSync } from "fs";
import { join } from "path";

import { describe, expect, it } from "vitest";

const SRC_ROOT = join(process.cwd(), "src");

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "") // 块注释（含 jsdoc——dialog-provider 的提及在此）
    .replace(/(^|\s)\/\/.*$/gm, "$1");
}

/** 禁区目录豁免：`src/components/ui`、`src/components/ai-elements`（registry 生成物，
 * 手改禁区——registry sync 拉入的生成代码不受本守卫约束）。按「父目录名 = components
 * 且目录名命中」判定，避免按名字全深度误豁免业务目录。 */
function isExemptDir(parentDir: string, name: string): boolean {
  return (
    (name === "ui" || name === "ai-elements") &&
    (parentDir.endsWith("components") ||
      parentDir === join(SRC_ROOT, "components"))
  );
}

function collectFiles(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (isExemptDir(dir, name)) continue;
      collectFiles(full, acc);
    } else if (/\.(ts|tsx)$/.test(name)) {
      acc.push(full);
    }
  }
  return acc;
}

describe("静态守卫：window.confirm 零回潮（OBS-3）", () => {
  it("src/（除 ui/、ai-elements/ 生成物禁区）无 window.confirm( 调用", () => {
    const offenders: string[] = [];
    for (const file of collectFiles(SRC_ROOT)) {
      const stripped = stripComments(readFileSync(file, "utf-8"));
      if (stripped.includes("window.confirm(")) {
        offenders.push(file.replace(join(process.cwd(), "/"), ""));
      }
    }
    expect(
      offenders,
      `发现 window.confirm 回潮：${offenders.join(", ")}`,
    ).toEqual([]);
  });
});
