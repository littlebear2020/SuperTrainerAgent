/**
 * B5 (2026-08-13): ContinueWritingButton 零测试补测。
 *
 * 覆盖三个行为面：
 * 1. 渲染 + 点击触发续写请求（streamAssist op=continue，mock api 层断言请求体）。
 * 2. unmount 时 abort 在飞请求（断言传给 streamAssist 的 signal.aborted 变 true）。
 * 3. 光标分支：光标在 codeBlock/table 内 → focus("end") + 追加文档末尾 + info toast；
 *    普通段落 → insertContentAt 光标处 + success toast。
 *
 * 仓库无 @testing-library/react：沿用 message-group-bash-step.dom.test.tsx 的
 * createRoot + act + happy-dom 手写 harness。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock("@/core/writer/api", () => ({
  buildSelectionContext: vi.fn(() => "文档标题：测试"),
  streamAssist: vi.fn(),
}));

import { ContinueWritingButton } from "@/components/workspace/writer/continue-writing-button";
import { streamAssist } from "@/core/writer/api";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockStreamAssist = vi.mocked(streamAssist);

/** Minimal TipTap editor fake — only the surface the component touches. */
function makeEditor(active: { codeBlock?: boolean; table?: boolean } = {}) {
  const chainObj = {
    run: vi.fn(),
    focus: vi.fn(),
    insertContent: vi.fn(),
    insertContentAt: vi.fn(),
  };
  // chain methods return the chain object so .focus(...).insertContent(...).run() works
  for (const key of ["focus", "insertContent", "insertContentAt"] as const) {
    chainObj[key].mockReturnValue(chainObj);
  }
  const editor = {
    state: {
      selection: { from: 10, to: 10 },
      doc: { textBetween: vi.fn(() => "已有文本") },
    },
    isActive: vi.fn((name: string) =>
      name === "codeBlock"
        ? (active.codeBlock ?? false)
        : name === "table"
          ? (active.table ?? false)
          : false,
    ),
    chain: vi.fn(() => chainObj),
  };
  return { editor: editor as never, chain: chainObj };
}

let container: HTMLDivElement;
let root: Root;
let caughtErrors: unknown[];

beforeEach(() => {
  caughtErrors = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container, {
    onCaughtError: (err) => caughtErrors.push(err),
    onUncaughtError: (err) => caughtErrors.push(err),
  });
  vi.clearAllMocks();
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  document.body.innerHTML = "";
});

function renderButton(editor: ReturnType<typeof makeEditor>["editor"]) {
  return act(async () => {
    root.render(
      <ContinueWritingButton
        editor={editor}
        docType="article"
        docTitle="测试文档"
        markdown="正文"
      />,
    );
  });
}

function clickButton(text: string) {
  const btn = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(text),
  );
  expect(btn, `button "${text}" should exist`).toBeTruthy();
  btn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

test("渲染与点击触发续写请求（op=continue）", async () => {
  mockStreamAssist.mockImplementation(async function* () {
    yield "续写";
    yield "内容";
  });
  const { editor, chain } = makeEditor();
  await renderButton(editor);

  await act(async () => {
    clickButton("续写");
  });
  await act(async () => {
    clickButton("开始续写");
  });

  expect(caughtErrors).toEqual([]);
  expect(mockStreamAssist).toHaveBeenCalledTimes(1);
  const [req, signal] = mockStreamAssist.mock.calls[0]!;
  expect(req.op).toBe("continue");
  expect(req.doc_type).toBe("article");
  expect(req.text).toBe("已有文本");
  expect(signal).toBeInstanceOf(AbortSignal);
  // 普通段落分支：insertContentAt 光标处 + success toast
  expect(chain.insertContentAt).toHaveBeenCalledWith(
    { from: 10, to: 10 },
    "\n\n续写内容",
  );
  expect(toast.success).toHaveBeenCalledWith("续写完成");
});

test("unmount 时 abort 在飞请求", async () => {
  const signals: Array<AbortSignal | undefined> = [];
  mockStreamAssist.mockImplementation(async function* (_req, signal) {
    signals.push(signal);
    // 挂起直到被 abort——模拟真实在飞流
    while (!signal?.aborted) {
      await new Promise((r) => setTimeout(r, 10));
    }
  });
  const { editor } = makeEditor();
  await renderButton(editor);

  await act(async () => {
    clickButton("续写");
  });
  await act(async () => {
    clickButton("开始续写");
  });
  expect(signals).toHaveLength(1);
  expect(signals[0]!.aborted).toBe(false);

  await act(async () => {
    root.unmount();
  });

  expect(signals[0]!.aborted).toBe(true);
});

test("光标在 codeBlock 内 → focus(end) 追加文档末尾 + info toast", async () => {
  mockStreamAssist.mockImplementation(async function* () {
    yield "续写内容";
  });
  const { editor, chain } = makeEditor({ codeBlock: true });
  await renderButton(editor);

  await act(async () => {
    clickButton("续写");
  });
  await act(async () => {
    clickButton("开始续写");
  });

  expect(chain.focus).toHaveBeenCalledWith("end");
  expect(chain.insertContent).toHaveBeenCalledWith("\n\n续写内容");
  expect(chain.insertContentAt).not.toHaveBeenCalled();
  expect(toast.info).toHaveBeenCalledWith("已将续写内容追加到文档末尾");
});

test("光标在 table 内 → 同样走 focus(end) 追加分支", async () => {
  mockStreamAssist.mockImplementation(async function* () {
    yield "续写内容";
  });
  const { editor, chain } = makeEditor({ table: true });
  await renderButton(editor);

  await act(async () => {
    clickButton("续写");
  });
  await act(async () => {
    clickButton("开始续写");
  });

  expect(chain.focus).toHaveBeenCalledWith("end");
  expect(chain.insertContentAt).not.toHaveBeenCalled();
  expect(toast.info).toHaveBeenCalledWith("已将续写内容追加到文档末尾");
});

test("光标在普通段落 → insertContentAt 光标处 + success toast", async () => {
  mockStreamAssist.mockImplementation(async function* () {
    yield "续写内容";
  });
  const { editor, chain } = makeEditor();
  await renderButton(editor);

  await act(async () => {
    clickButton("续写");
  });
  await act(async () => {
    clickButton("开始续写");
  });

  expect(chain.insertContentAt).toHaveBeenCalledWith(
    { from: 10, to: 10 },
    "\n\n续写内容",
  );
  expect(chain.focus).not.toHaveBeenCalledWith("end");
  expect(toast.success).toHaveBeenCalledWith("续写完成");
});
