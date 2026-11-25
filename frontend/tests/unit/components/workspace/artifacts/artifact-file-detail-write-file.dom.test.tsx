/**
 * PR4（2026-11-24 前端可靠性批）：write-file 引用在详情面板里的解析与正文来源。
 * harness 参照 artifact-file-detail-discard-confirm.dom.test.tsx（无 @testing-library/react）。
 */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  thread: { messages: [] as unknown[], isLoading: false },
  tBase: {} as Record<string, unknown>,
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ getQueryData: vi.fn(), setQueryData: vi.fn() }),
  useQuery: () => ({ data: undefined, isLoading: false, error: null }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: h.tBase }),
}));

vi.mock("@/env", () => ({
  env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" },
}));

// CodeMirror 重库：用只读 textarea 暴露 value（断言正文来源）
vi.mock("next/dynamic", () => ({
  default: () => (props: Record<string, unknown>) => (
    <textarea
      data-testid="code-editor"
      readOnly
      value={props.value as string}
    />
  ),
}));

vi.mock("streamdown", () => ({
  Streamdown: () => null,
}));

vi.mock("@/core/streamdown", () => ({
  streamdownPlugins: [],
}));

vi.mock("@/core/skills/api", () => ({
  installSkill: vi.fn(),
}));

vi.mock("@/components/workspace/artifacts/context", () => ({
  useArtifacts: () => ({
    artifacts: [],
    setOpen: vi.fn(),
    select: vi.fn(),
    drafts: {},
    setDrafts: vi.fn(),
    editingPath: null,
    setEditingPath: vi.fn(),
  }),
}));

vi.mock("@/components/workspace/messages/context", () => ({
  useThread: () => ({ thread: h.thread }),
}));

vi.mock("@/components/workspace/tooltip", () => ({
  Tooltip: (p: Record<string, unknown>) => <div>{p.children as ReactNode}</div>,
}));

vi.mock("@/components/workspace/citations/artifact-link", () => ({
  ArtifactLink: () => null,
}));

vi.mock("@/components/ai-elements/artifact", () => ({
  Artifact: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactHeader: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactTitle: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactActions: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactAction: (p: Record<string, unknown>) => (
    <button
      type="button"
      disabled={p.disabled as boolean | undefined}
      data-action={p.label as string}
    >
      {p.label as string}
    </button>
  ),
  ArtifactContent: (p: Record<string, unknown>) => <div {...p} />,
}));

vi.mock("@/components/ui/select", () => ({
  Select: () => <div data-testid="artifact-select" />,
  SelectContent: () => null,
  SelectGroup: () => null,
  SelectItem: () => null,
  SelectTrigger: () => null,
  SelectValue: () => null,
}));

vi.mock("@/components/ui/toggle-group", () => ({
  ToggleGroup: (p: Record<string, unknown>) => <div {...p} />,
  ToggleGroupItem: () => null,
}));

import { ArtifactFileDetail } from "@/components/workspace/artifacts/artifact-file-detail";
import { DialogProvider } from "@/components/workspace/dialog-provider";
import { zhCN } from "@/core/i18n/locales/zh-CN";

h.tBase = zhCN as unknown as Record<string, unknown>;

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const aiWithWrite = (toolCallId: string, path: string, content: string) => ({
  id: "msg-1",
  type: "ai",
  content: "",
  tool_calls: [{ id: toolCallId, name: "write_file", args: { path, content } }],
});

describe("write-file 引用在详情面板里的解析（纵切 A：过渡签名）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.thread = { messages: [], isLoading: false };
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.body.innerHTML = "";
  });

  async function renderDetail(filepath: string) {
    await act(async () => {
      root.render(
        <DialogProvider>
          <ArtifactFileDetail filepath={filepath} threadId="th_1" />
        </DialogProvider>,
      );
    });
  }

  it("新格式引用：特殊文件名的标题正确，正文来自定位到的 tool call 的内联快照", async () => {
    h.thread.messages = [
      aiWithWrite("call-1", "/mnt/data/100% final#1.txt", "正文内容"),
    ];
    await renderDetail(
      "write-file:?path=%2Fmnt%2Fdata%2F100%25%20final%231.txt&message_id=msg-1&tool_call_id=call-1",
    );
    expect(container.textContent).toContain("100% final#1.txt");
    const editor = container.querySelector<HTMLTextAreaElement>(
      "[data-testid='code-editor']",
    );
    expect(editor?.value).toBe("正文内容");
  });

  it("引用非法或 path 与 tool call 不一致：不渲染正文", async () => {
    h.thread.messages = [aiWithWrite("call-1", "/mnt/data/a.txt", "正文内容")];
    await renderDetail(
      "write-file:?path=%2Fmnt%2Fdata%2Fother.txt&message_id=msg-1&tool_call_id=call-1",
    );
    const editor = container.querySelector<HTMLTextAreaElement>(
      "[data-testid='code-editor']",
    );
    expect(editor?.value).toBe("");
  });
});
