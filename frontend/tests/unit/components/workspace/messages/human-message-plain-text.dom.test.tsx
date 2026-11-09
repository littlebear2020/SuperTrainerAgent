/**
 * User messages must render as plain text, verbatim.
 *
 * The composer sends plain text, but the rendered bubble ran it through a
 * Markdown parser, so pasted logs and code came back mangled:
 *   `__init__` → `init` (every Python traceback silently lost its dunders)
 *   `**CRITICAL**` → CRITICAL (asterisks eaten)
 *   `# 注释` → an <h1>
 *   `$5 … $9` → swallowed by KaTeX as math
 *   multi-line logs → collapsed onto one line
 *
 * Product decision (2026-08-04): render user messages as plain text and drop
 * `humanMessagePlugins` entirely. Math in *user* messages is knowingly given
 * up; AI replies still render Markdown and math as before.
 *
 * The repo has no @testing-library/react, so this uses the createRoot + act +
 * happy-dom harness that the sibling component tests use. Asserting on
 * `textContent` and tag names is what actually pins the behaviour: a plain
 * <p>/<div> of text, never <h1>/<strong>/<em>.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// Streamdown imports katex CSS at module scope and vitest has no CSS
// transform, so it must be stubbed. The stub applies a crude Markdown
// transform rather than passing text through: that way these tests fail
// against the old behaviour for the *same reason* production did (text was
// parsed as Markdown), instead of passing vacuously. The `data-markdown-
// renderer` marker additionally pins that the human path does not reach a
// Markdown renderer at all.
vi.mock("streamdown", () => ({
  Streamdown: ({ children }: { children?: unknown }) => {
    const source = typeof children === "string" ? children : "";
    const mangled = source
      .replace(/__(\w+)__/g, "$1") // dunder eaten by emphasis
      .replace(/\*\*(.+?)\*\*/g, "$1") // ** eaten by strong
      .replace(/^#\s+/gm, "") // leading # becomes a heading
      .replace(/\$(\d)/g, "") // $ swallowed by KaTeX
      .replace(/\n+/g, " "); // lines collapsed into one
    return <div data-markdown-renderer="true">{mangled}</div>;
  },
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      clipboard: { copyToClipboard: "复制", copied: "已复制" },
      messages: { share: "分享", feedback: "反馈" },
    },
  }),
}));

import { MessageListItem } from "@/components/workspace/messages/message-list-item";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

function renderHumanMessage(content: string): HTMLElement {
  act(() => {
    root.render(
      <MessageListItem
        message={{
          id: "m1",
          type: "human",
          content,
        }}
        threadId="t1"
      />,
    );
  });
  return container;
}

describe("human message rendering", () => {
  test("preserves dunder names instead of eating the underscores", () => {
    const el = renderHumanMessage("def __init__(self): pass");
    expect(el.textContent).toContain("__init__");
    expect(el.querySelector("em")).toBeNull();
  });

  test("preserves double asterisks instead of bolding", () => {
    const el = renderHumanMessage("**CRITICAL** failure in module");
    expect(el.textContent).toContain("**CRITICAL**");
    expect(el.querySelector("strong")).toBeNull();
  });

  test("does not turn a leading hash into a heading", () => {
    const el = renderHumanMessage("# 注释：这是一行普通文本");
    expect(el.textContent).toContain("# 注释");
    expect(el.querySelector("h1")).toBeNull();
  });

  test("keeps dollar amounts instead of parsing them as math", () => {
    const el = renderHumanMessage("Prices ranged from $5 to $9 per unit");
    expect(el.textContent).toContain("$5");
    expect(el.textContent).toContain("$9");
    expect(el.querySelector(".katex")).toBeNull();
  });

  test("preserves multi-line log output verbatim", () => {
    const log = [
      "Traceback (most recent call last):",
      '  File "app.py", line 42, in __init__',
      "    raise ValueError(msg)",
      "ValueError: bad input",
    ].join("\n");

    const el = renderHumanMessage(log);

    expect(el.textContent).toContain("__init__");
    expect(el.textContent).toContain('File "app.py", line 42');
    expect(el.textContent).toContain("ValueError: bad input");
  });

  test("does not route user text through the Markdown renderer at all", () => {
    const el = renderHumanMessage("**bold** _italic_ # heading");
    expect(el.querySelector("[data-markdown-renderer]")).toBeNull();
  });

  test("renders ordinary prose unchanged", () => {
    const el = renderHumanMessage("帮我把这个函数改成异步的");
    expect(el.textContent).toContain("帮我把这个函数改成异步的");
  });

  test("renders nothing extra for an empty message", () => {
    const el = renderHumanMessage("");
    expect(el.textContent?.trim()).toBe("");
  });
});
