/**
 * DiffView render smoke test (Task W8).
 *
 * The component is a thin presentational wrapper over `computeDiff` (fully
 * tested in compute-diff.test.ts). @testing-library/react isn't a dep, so we
 * smoke-test rendering via react-dom/server's renderToStaticMarkup (DOM-free,
 * matches the folder's DOM-free test convention). Asserts the segment→class
 * mapping: removed → red/line-through, added → green, unchanged → muted.
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { test, expect } from "vitest";

import { DiffView } from "@/components/workspace/writer/diff-view";

test("diff-view renders removed as red+strikethrough, added as green, unchanged as muted", () => {
  // 旧内容 → 新内容: removed '旧', added '新', unchanged '内容'
  const html = renderToStaticMarkup(
    createElement(DiffView, { original: "旧内容", revised: "新内容" }),
  );

  // Removed segment: red + strikethrough.
  expect(html).toContain("旧");
  expect(html).toContain("line-through");
  expect(html).toContain("red");
  // Added segment: green.
  expect(html).toContain("新");
  expect(html).toContain("green");
  // Unchanged segment: muted.
  expect(html).toContain("内容");
  expect(html).toContain("muted-foreground");
});

test("diff-view renders nothing for identical text (no added/removed classes)", () => {
  const html = renderToStaticMarkup(
    createElement(DiffView, { original: "相同", revised: "相同" }),
  );
  // All unchanged → muted class, NO line-through / green.
  expect(html).toContain("muted-foreground");
  expect(html).not.toContain("line-through");
  expect(html).not.toContain("green");
});
