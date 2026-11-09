import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { shouldDeriveTitleFromH1 } from "@/components/workspace/writer/writer-page";

/**
 * P1-7: every autosave rederived title from the body's first H1 and overwrote
 * the stored title. A user who renamed in the title bar had their rename
 * reverted on the next keystroke. shouldDeriveTitleFromH1 gates the write on
 * a "manually renamed" flag.
 */
describe("shouldDeriveTitleFromH1 (P1-7)", () => {
  it("用户手动改名后，不再用 H1 覆盖", () => {
    expect(
      shouldDeriveTitleFromH1("# 产品介绍", "产品介绍 v2 终稿", true),
    ).toBe(false);
  });

  it("未手动改名、H1 与现 title 不同 → 派生写入", () => {
    expect(shouldDeriveTitleFromH1("# 产品介绍", "未命名文章", false)).toBe(
      true,
    );
  });

  it("派生值与现值相同 → 无需写", () => {
    expect(shouldDeriveTitleFromH1("# 产品介绍", "产品介绍", false)).toBe(
      false,
    );
  });

  it("无 H1、fallback=现值 → 相同 → 不写", () => {
    expect(shouldDeriveTitleFromH1("正文无标题", "正文无标题", false)).toBe(
      false,
    );
  });
});

/**
 * P2-2 / BUG-004（2026-09-14）：重命名 → 空文档覆盖链——静态守卫（源码文本锁）。
 * P2-3 / BUG-005：writer-page 挂载 ShareDialog。
 */
describe("writer-page 静态守卫（P2-2/BUG-004 + P2-3/BUG-005）", () => {
  const src = readFileSync(
    join(process.cwd(), "src/components/workspace/writer/writer-page.tsx"),
    "utf-8",
  );

  it("缺陷 a：rename input 的 keydown 入口 stopPropagation（全键，非仅 Enter/Escape 分支）", () => {
    const m =
      /onKeyDown=\{\(e\) => \{[\s\S]{0,300}?e\.stopPropagation\(\);/.exec(src);
    expect(
      m,
      "rename input onKeyDown 入口应有 e.stopPropagation()",
    ).not.toBeNull();
    // onKeyUp/onKeyPress 同步补齐（Space 等键冒泡同样可触发卡片 onKeyDown）
    expect(src).toMatch(/onKeyUp=\{\(e\) => e\.stopPropagation\(\)\}/);
    expect(src).toMatch(/onKeyPress=\{\(e\) => e\.stopPropagation\(\)\}/);
  });

  it("缺陷 b：docLoaded state 门控——加载完成前 TiptapEditor 不挂载", () => {
    expect(src).toContain("const [docLoaded, setDocLoaded] = useState(false)");
    expect(src).toMatch(/!docLoaded/);
    // setDocLoaded(true) 在首载内容确定后（setMarkdown 同一收尾位置）
    expect(src).toContain("setMarkdown(content)");
    expect(src).toContain("setDocLoaded(true)");
  });

  it("缺陷 b：triggerSave 的 version 守卫（typeof number，防无 CAS legacy 路径）", () => {
    expect(src).toContain('typeof currentDoc?.version !== "number"');
  });

  it("缺陷 b：commitTitle / handleDocTypeChange 的 !currentDoc 守卫", () => {
    const commit = src.slice(
      src.indexOf("const commitTitle"),
      src.indexOf("// ── Render"),
    );
    expect(commit).toContain("if (!currentDoc) return");
    const typeChange = src.slice(
      src.indexOf("const handleDocTypeChange"),
      src.indexOf("// Stats"),
    );
    expect(typeChange).toContain("if (!currentDoc) return");
  });

  it("P2-3：writer-page import 并渲染 ShareDialog + 分享按钮", () => {
    expect(src).toContain("ShareDialog");
    expect(src).toMatch(/<ShareDialog\b/);
    expect(src).toContain("shareOpen");
  });
});
