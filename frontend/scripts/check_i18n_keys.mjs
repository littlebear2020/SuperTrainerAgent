#!/usr/bin/env node
/**
 * i18n key 存活检测（2026-10-06 防复发批）。
 *
 * 方法 = 2026-10-05 复核口径的机械化（第 1A 批执行时验证过的同名脚本）：
 * - TS compiler 解析 Translations 接口 → 全部叶子路径
 * - 全仓收集 t.* 属性访问链；别名解析覆盖：
 *   ①useI18n/getI18n 多属性解构与改名 ②Translations["sec"] 类型参数及其
 *   type 别名 ③const/JSX 再绑定（vt={t.voiceTwin} 等，迭代传递）
 *   ④enUS/zhCN 字典直引
 * - 动态索引白名单（t.x.y[ 形态的子树全活）：
 *   t.sidebar[labelKey]（command-palette / workspace-nav-chat-list）、
 *   t.inputBox[mode 键]（mode-hover-guide）、t.subtasks[status]
 *   （subtask-card）、t.settings.memory.markdown.table.confidenceLevel[key]
 *   （memory-settings-page）
 * - 判活：叶子完整路径被用，或任一真前缀被动态索引。
 *
 * 退出码：发现死 key → 1（CI 拦截）；全活 → 0。
 * 用法：node scripts/check_i18n_keys.mjs（frontend 目录下跑）。
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ts =
  (await import(path.join(FE, "node_modules/typescript/lib/typescript.js")))
    .default ??
  (await import(path.join(FE, "node_modules/typescript/lib/typescript.js")));

// ── 1. Translations 叶子路径 ──
const typesPath = path.join(FE, "src/core/i18n/locales/types.ts");
const src = readFileSync(typesPath, "utf8");
const sf = ts.createSourceFile(typesPath, src, ts.ScriptTarget.Latest, true);
let iface;
for (const st of sf.statements)
  if (ts.isInterfaceDeclaration(st) && st.name.text === "Translations")
    iface = st;
if (!iface) {
  console.error("Translations interface not found");
  process.exit(1);
}
const leaves = [];
(function walk(node, prefix) {
  for (const m of node.members) {
    if (!ts.isPropertySignature(m) || !ts.isIdentifier(m.name)) continue;
    const p = prefix ? `${prefix}.${m.name.text}` : m.name.text;
    if (m.type && ts.isTypeLiteralNode(m.type)) walk(m.type, p);
    else leaves.push(p);
  }
})(iface, "");

// ── 2. 使用链收集 ──
function* walkFiles(dir) {
  for (const e of readdirSync(dir)) {
    const fp = path.join(dir, e);
    if (statSync(fp).isDirectory()) {
      if (e === "locales" && dir.endsWith("i18n")) continue; // 字典本体不算使用方
      if (e === "node_modules" || e === ".next" || e === ".superpowers")
        continue;
      yield* walkFiles(fp);
    } else if (/\.(tsx?|mts|mjs)$/.test(e)) yield fp;
  }
}
const used = new Set();
const indexed = new Set();
for (const fp of walkFiles(path.join(FE, "src"))) {
  const text = readFileSync(fp, "utf8");
  const aliases = new Map();
  // ① useI18n/getI18n 解构（支持多属性 { t, locale, ... } 与改名 { t: x }）与整取
  for (const m of text.matchAll(
    /(?:const|let)\s*\{([^}]*)\}\s*=\s*(?:await\s+)?(?:useI18n|getI18n)\(\)/g,
  )) {
    for (const part of m[1].split(",")) {
      const pm = part.trim().match(/^(?:t|t\s*:\s*([A-Za-z_$][\w$]*))$/);
      if (pm) aliases.set(pm[1] ?? "t", "");
    }
  }
  for (const m of text.matchAll(
    /(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?(?:useI18n|getI18n)\(\)/g,
  ))
    aliases.set(`${m[1]}.t`, "");
  // Translations["sec"] / Translations 类型参数与 type 别名
  const typeAliases = new Map();
  for (const m of text.matchAll(
    /type\s+([A-Za-z_$][\w$]*)\s*=\s*Translations(?:\["([\w$]+)"\])?\s*;/g,
  ))
    typeAliases.set(m[1], m[2] ?? "");
  for (const m of text.matchAll(
    /([A-Za-z_$][\w$]*)\s*:\s*Translations(?:\["([\w$]+)"\])?/g,
  ))
    aliases.set(m[1], m[2] ?? "");
  if (typeAliases.size)
    for (const m of text.matchAll(
      new RegExp(
        `([A-Za-z_$][\\w$]*)\\s*:\\s*(${[...typeAliases.keys()].join("|")})\\b`,
        "g",
      ),
    ))
      aliases.set(m[1], typeAliases.get(m[2]));
  if (aliases.size === 0) continue;
  // 再绑定（迭代）
  function resolvePath(p) {
    const parts = p.split(".");
    for (let take = parts.length - 1; take >= 1; take--) {
      const k = parts.slice(0, take).join(".");
      if (aliases.has(k))
        return [
          ...aliases.get(k).split(".").filter(Boolean),
          ...parts.slice(take),
        ].join(".");
    }
    return aliases.has(p) ? aliases.get(p) : undefined;
  }
  for (let round = 0; round < 4; round++) {
    let changed = false;
    for (const m of text.matchAll(
      /([A-Za-z_$][\w$]*)\s*=\s*\{?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\}?/g,
    )) {
      const off = resolvePath(m[2]);
      if (off === undefined) continue;
      if (!aliases.has(m[1]) || aliases.get(m[1]) === "") {
        aliases.set(m[1], off);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const bases = [...aliases.keys()]
    .map((a) => a.replace(".", "\\."))
    .sort((a, b) => b.length - a.length)
    .join("|");
  const chainRe = new RegExp(
    `(?<![\\w$.])(?:${bases})((?:\\.[A-Za-z_$][\\w$]*)+)`,
    "g",
  );
  for (const m of text.matchAll(chainRe)) {
    const base = m[0].replace(/((?:\.[A-Za-z_$][\w$]*)+)$/, "");
    const off = aliases.get(base) ?? "";
    const segs = m[1].slice(1).split(".");
    for (let i = 1; i <= segs.length; i++)
      used.add(
        [...off.split(".").filter(Boolean), ...segs.slice(0, i)].join("."),
      );
  }
  const idxRe = new RegExp(
    `(?<![\\w$.])(?:${bases})((?:\\.[A-Za-z_$][\\w$]*)+)\\s*\\[`,
    "g",
  );
  for (const m of text.matchAll(idxRe)) indexed.add(m[1].slice(1));
}
// ── 3. 字典直引（enUS/zhCN 从任意模块 import）──
for (const fp of walkFiles(path.join(FE, "src"))) {
  const text = readFileSync(fp, "utf8");
  for (const m of text.matchAll(/import\s*\{([^}]+)\}\s*from\s*"[^"]*"/g)) {
    for (const part of m[1].split(",")) {
      const n = part
        .trim()
        .split(/\s+as\s+/)[0]
        .replace(/^type\s+/, "");
      if (n !== "enUS" && n !== "zhCN") continue;
      const as = part
        .trim()
        .split(/\s+as\s+/)
        .pop();
      for (const c of text.matchAll(
        new RegExp(`(?<![\\w$.])${as}((?:\\.[A-Za-z_$][\\w$]*)+)`, "g"),
      ))
        for (let i = 1; i <= c[1].slice(1).split(".").length; i++)
          used.add(c[1].slice(1).split(".").slice(0, i).join("."));
    }
  }
}

// ── 3.5 结构化消费者的显式豁免 ──
// inline 结构类型参数（非 Translations 引用）持有 key 名的已知盲区——属性访问
// 形如 c.errorNoDatabase，别名追踪不可达。2026-10-06 第 1A 批 tsc 证伪过
// （当时差点当死 key 删掉）；新增此类消费须在此登记，否则脚本误报。
const STRUCTURAL_CONSUMERS = [
  "console.errorNoDatabase", // core/console/errors.ts:18 c.errorNoDatabase
  "console.errorForbidden", // core/console/errors.ts:19 c.errorForbidden
];

// ── 4. 判活 ──
const dead = leaves.filter((leaf) => {
  if (used.has(leaf) || STRUCTURAL_CONSUMERS.includes(leaf)) return false;
  const parts = leaf.split(".");
  for (let i = 1; i < parts.length; i++)
    if (indexed.has(parts.slice(0, i).join("."))) return false;
  return true;
});

if (dead.length > 0) {
  console.error(
    `i18n 死 key ${dead.length} 个（删除需同步 types.ts / en-US.ts / zh-CN.ts）：`,
  );
  for (const d of dead) console.error(`  ${d}`);
  console.error(
    "注意动态拼接访问须以 t.x.y[ 形态出现在源码中（本脚本按此白名单），字符串拼 key 无法识别。",
  );
  process.exit(1);
}
console.log(`i18n key 存活检测通过：${leaves.length} 个叶子全活。`);
