/**
 * P0-2: MarkdownPreview must sanitize raw HTML (rehype-sanitize).
 * Doc content (LLM output / pasted HTML) flows through rehype-raw into three
 * surfaces — share page / version preview / writer page — so a poisoned doc
 * is a stored-XSS vector without a sanitize step.
 *
 * Test 3 note (D-3 update): the auto-annotation feature (SOUL teaching +
 * AutoMark + RoughAnnotation renderer) was removed in D-3 (2026-07-27).
 * Legacy annotation spans in existing docs now degrade to plain spans; this
 * test pins that the sanitize schema still preserves the harmless
 * data-rough-notation carrier attribute (earlier amendment history: the
 * original mock-based assertion was replaced because vi.mock via "@/" alias
 * does not intercept the component's relative import — probe-verified).
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MarkdownPreview } from "@/components/workspace/writer/markdown-preview";

describe("MarkdownPreview sanitize (P0-2)", () => {
  it("strips <script> and onerror handler from raw HTML", () => {
    const html = renderToStaticMarkup(
      <MarkdownPreview
        content={
          '<img src="x" onerror="alert(1)"><script>alert(2)</script>正文'
        }
      />,
    );
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("<script");
    expect(html).toContain("正文");
  });

  it("strips javascript: links", () => {
    const html = renderToStaticMarkup(
      <MarkdownPreview content={"[点我](javascript:alert(1))"} />,
    );
    expect(html).not.toContain("javascript:");
  });

  it("keeps data-rough-notation on spans (legacy annotation spans degrade, attr preserved)", () => {
    // D-3 removed the annotation feature (SOUL teaching + renderer);存量文档
    // 里遗留的标注 span 现在降级为普通 span。T8 的保证不变：sanitize schema
    // 必须保留 data-rough-notation 载体属性（data-* 无害），剥掉的是危险面。
    const legacy = renderToStaticMarkup(
      <MarkdownPreview
        content={'<span data-rough-notation="circle">重点</span>'}
      />,
    );
    expect(legacy).toContain('data-rough-notation="circle"');
    expect(legacy).toContain("重点");
  });

  it("keeps KaTeX output classes (math still renders)", () => {
    const html = renderToStaticMarkup(<MarkdownPreview content={"$x^2$"} />);
    expect(html).toContain("katex");
  });
});
