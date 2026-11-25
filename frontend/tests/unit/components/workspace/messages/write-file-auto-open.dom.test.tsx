/**
 * PR4（2026-11-24 前端可靠性批）纵切 C：write_file / str_replace 工具调用的 auto-open
 * 竞态。流式里参数是一段段到的：半截 path 不能构造引用、不能选中；参数完整后才试
 * 一次，用户在这 100ms 里手选了就不抢；卸载不留 timer。hook 侧：run 还没 onFinish
 * 时 result 一到，pending 就能过渡到 ready / failed。
 *
 * 仓库无 @testing-library/react：createRoot + act + happy-dom 手写 harness。
 */

import { AIMessageChunk } from "@langchain/core/messages";
import type { Message } from "@langchain/langgraph-sdk";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactElement, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { MessageGroup } from "@/components/workspace/messages/message-group";
import { WriteFileToolCall } from "@/components/workspace/messages/write-file-tool-call";
import { useArtifactContent as useArtifactContentForTest } from "@/core/artifacts/hooks";

const h = vi.hoisted(() => ({
  artifacts: {
    setOpen: vi.fn(),
    autoOpen: true,
    autoSelect: true,
    selectedArtifact: null as string | null,
    select: vi.fn(),
  },
  thread: {
    messages: [] as unknown[],
    isThreadLoading: false,
    isLoading: false,
  },
  history: {
    isHistoryLoading: false,
    hasMoreHistory: false,
    refetchHistory: vi.fn(async () => undefined),
  },
}));

vi.mock("@/components/workspace/artifacts", () => ({
  useArtifacts: () => h.artifacts,
}));

vi.mock("@/components/workspace/messages/context", () => ({
  useThread: () => ({ thread: h.thread, ...h.history }),
  ThreadContext: { Provider: (p: Record<string, unknown>) => p.children },
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      toolCalls: { writeFile: "写入文件" },
      writeFileRef: {
        pending: "读取中",
        notFound: "没有找到",
        loadFullHistory: "加载完整历史",
        retry: "重试",
        invalid: "引用无效",
        failed: "写入失败",
      },
    },
  }),
}));

vi.mock("@/components/ai-elements/chain-of-thought", () => ({
  ChainOfThought: (p: Record<string, unknown>) => (
    <div data-testid="cot">{p.children as React.ReactNode}</div>
  ),
  ChainOfThoughtContent: (p: Record<string, unknown>) => (
    <div>{p.children as React.ReactNode}</div>
  ),
  ChainOfThoughtSearchResults: (p: Record<string, unknown>) => (
    <div>{p.children as React.ReactNode}</div>
  ),
  ChainOfThoughtStep: (p: Record<string, unknown>) => (
    <button
      type="button"
      data-testid="step"
      className={p.className as string | undefined}
      onClick={p.onClick as (() => void) | undefined}
    >
      {p.label as string}
    </button>
  ),
  ChainOfThoughtSearchResult: (p: Record<string, unknown>) => (
    <div data-testid="result">{p.children as React.ReactNode}</div>
  ),
}));

vi.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ code }: { code: string }) => <pre>{code}</pre>,
}));

vi.mock("@/components/workspace/messages/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <div>{content}</div>,
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://backend",
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

/** 流式 chunk 的原始 args：在 `cut` 处截断。 */
function rawArgs(cut?: number) {
  const full = JSON.stringify({
    description: "写报告",
    path: "/mnt/data/100% final#1?.md",
    content: "正文",
  });
  return cut === undefined ? full : full.slice(0, cut);
}

const COMPLETE = JSON.parse(rawArgs()) as Record<string, unknown>;

/**
 * 半截参数时 SDK 交付的 `args`：`@langchain/core` 的 collapseToolCallChunks 用的是宽松的
 * 部分 JSON 解析，没拼完的 path 也作为一个完整的字符串出现在 `args` 里（实测输出）。
 */
const PARTIAL = {
  description: "写报告",
  path: "/mnt/data/100%",
} as Record<string, unknown>;

function renderInto(element: ReactElement, container: HTMLElement) {
  const root = createRoot(container);
  act(() => {
    root.render(element);
  });
  return root;
}

describe("WriteFileToolCall：完整参数前不选中、不 auto-open", () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    vi.useFakeTimers();
    h.artifacts.select.mockClear();
    h.artifacts.setOpen.mockClear();
    h.artifacts.autoOpen = true;
    h.artifacts.autoSelect = true;
    h.artifacts.selectedArtifact = null;
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    root = null;
    container.remove();
    vi.useRealTimers();
  });

  test("path 中间及 % / # / ? 前被切开的 chunk：半截参数不构造引用、select 为零", async () => {
    const cutInsidePath = rawArgs().indexOf("100% final") + 5;
    expect(cutInsidePath).toBeGreaterThan(0);
    // SDK 的 args 里已经是半截 path 的对象：只认原始串的严格解析
    root = renderInto(
      <WriteFileToolCall
        id="call-1"
        messageId="msg-1"
        args={PARTIAL}
        rawArgs={rawArgs(cutInsidePath)}
        isLoading
        isLast
      />,
      container,
    );
    await act(async () => {
      vi.advanceTimersByTime(400);
    });
    expect(h.artifacts.select).not.toHaveBeenCalled();
    expect(h.artifacts.setOpen).not.toHaveBeenCalled();
    // 半截参数时点步骤也不构造 URL
    container.querySelector<HTMLButtonElement>("[data-testid='step']")?.click();
    expect(h.artifacts.select).not.toHaveBeenCalled();
  });

  test("参数完整后只选择一次：引用按 query 参数编码，同 toolCallId 不重复", async () => {
    root = renderInto(
      <WriteFileToolCall
        id="call-1"
        messageId="msg-1"
        args={COMPLETE}
        rawArgs={rawArgs()}
        isLoading
        isLast
      />,
      container,
    );
    // 100ms 之内还没选
    await act(async () => {
      vi.advanceTimersByTime(50);
    });
    expect(h.artifacts.select).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(100);
    });
    expect(h.artifacts.select).toHaveBeenCalledTimes(1);
    const url = h.artifacts.select.mock.calls[0]![0] as string;
    expect(url).toMatch(/^write-file:\?/);
    expect(new URL(url).searchParams.get("path")).toBe(
      "/mnt/data/100% final#1?.md",
    );
    expect(h.artifacts.setOpen).toHaveBeenCalledWith(true);

    // 无关的 Provider 重渲染 / 计时器继续走：不再有第二次尝试
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    act(() => {
      root!.render(
        <WriteFileToolCall
          id="call-1"
          messageId="msg-1"
          args={COMPLETE}
          isLoading
          isLast
        />,
      );
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(h.artifacts.select).toHaveBeenCalledTimes(1);
  });

  test("100ms 内用户手选了别的产物：不抢选", async () => {
    root = renderInto(
      <WriteFileToolCall
        id="call-1"
        messageId="msg-1"
        args={COMPLETE}
        isLoading
        isLast
      />,
      container,
    );
    // 用户在计时器触发前手选（selectedArtifact 变化 → Provider 重渲染）
    act(() => {
      h.artifacts.selectedArtifact = "mnt/other.txt";
      root!.render(
        <WriteFileToolCall
          id="call-1"
          messageId="msg-1"
          args={COMPLETE}
          isLoading
          isLast
        />,
      );
    });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(h.artifacts.select).not.toHaveBeenCalled();
    expect(h.artifacts.setOpen).not.toHaveBeenCalled();
  });

  test("卸载后不留 timer：卸载后再走 1 秒也没有选中", async () => {
    root = renderInto(
      <WriteFileToolCall
        id="call-1"
        messageId="msg-1"
        args={COMPLETE}
        isLoading
        isLast
      />,
      container,
    );
    await act(async () => {
      vi.advanceTimersByTime(50);
    });
    await act(async () => root!.unmount());
    root = null;
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(h.artifacts.select).not.toHaveBeenCalled();
    expect(h.artifacts.setOpen).not.toHaveBeenCalled();
  });

  test("缺任一定位字段：不构造 URL，点步骤也不选中", async () => {
    root = renderInto(
      <>
        <WriteFileToolCall
          id=""
          messageId="msg-1"
          args={COMPLETE}
          isLoading
          isLast
        />
        <WriteFileToolCall
          id="call-2"
          messageId=""
          args={COMPLETE}
          isLoading
          isLast
        />
      </>,
      container,
    );
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    for (const step of container.querySelectorAll("[data-testid='step']")) {
      step.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    }
    expect(h.artifacts.select).not.toHaveBeenCalled();
    expect(h.artifacts.setOpen).not.toHaveBeenCalled();
  });

  test("autoOpen / autoSelect 被用户关掉后不再自动选中（历史里的完整 tool_calls 仍可点）", async () => {
    h.artifacts.autoSelect = false;
    root = renderInto(
      <WriteFileToolCall
        id="call-1"
        messageId="msg-1"
        args={COMPLETE}
        isLoading={false}
        isLast
      />,
      container,
    );
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(h.artifacts.select).not.toHaveBeenCalled();
    container.querySelector<HTMLButtonElement>("[data-testid='step']")?.click();
    expect(h.artifacts.select).toHaveBeenCalledTimes(1);
    expect(h.artifacts.setOpen).toHaveBeenCalledWith(true);
  });

  test("原始串不是合法 JSON、但工具已经执行完：以 SDK 宽松解析出的 args 为准，可点", async () => {
    // 模型产出的 args 本身就非法时，刷新后（没有 chunk）能打开、会话内却永远点不了——
    // result 到了就说明参数已定型，不再要求严格 JSON
    h.artifacts.autoOpen = false;
    h.artifacts.autoSelect = false;
    const malformed =
      '{"description":"写报告","path":"/mnt/data/100% final#1?.md","content":"正文"';
    root = renderInto(
      <WriteFileToolCall
        id="call-1"
        messageId="msg-1"
        args={COMPLETE}
        rawArgs={malformed}
        result="OK"
        isLoading={false}
        isLast
      />,
      container,
    );
    container.querySelector<HTMLButtonElement>("[data-testid='step']")!.click();
    expect(h.artifacts.select).toHaveBeenCalledTimes(1);
    expect(
      new URL(h.artifacts.select.mock.calls[0]![0] as string).searchParams.get(
        "path",
      ),
    ).toBe("/mnt/data/100% final#1?.md");
  });
});

describe("MessageGroup：tool_call_chunks 的原始串交给 write-file 步骤", () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  /**
   * SDK 交付的流式消息：MessageTupleManager 把每个片段转成 AIMessageChunk 再 concat。
   * `tool_calls` 是宽松解析的结果（半截 path 也在里面），`tool_call_chunks` 是原始串。
   * `cut` = 参数还没到齐时已收到的部分。
   */
  function streamedMessage(cut?: number): Message {
    const full = rawArgs();
    const chunk = new AIMessageChunk({
      id: "msg-1",
      content: "",
      tool_call_chunks: [
        {
          id: "call-1",
          name: "write_file",
          args: cut === undefined ? full : full.slice(0, cut),
          index: 0,
          type: "tool_call_chunk",
        },
      ],
    });
    return { ...chunk.toDict().data, type: "ai" } as unknown as Message;
  }

  beforeEach(() => {
    h.artifacts.select.mockClear();
    h.artifacts.setOpen.mockClear();
    h.artifacts.autoOpen = false;
    h.artifacts.autoSelect = false;
    h.artifacts.selectedArtifact = null;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = null;
  });

  afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    root = null;
    container.remove();
  });

  test("半截 path：SDK 的 args 里已是半截对象，步骤可点但不构造引用、不选中", async () => {
    const cutInsidePath = rawArgs().indexOf("100% final") + 5;
    const message = streamedMessage(cutInsidePath);
    // 前提：SDK 交付的 args 里已经是半截 path（宽松解析），原始串还没拼完
    expect(
      (message as unknown as { tool_calls: { args: { path: string } }[] })
        .tool_calls[0]!.args.path,
    ).toBe("/mnt/data/100%");
    root = renderInto(
      <MessageGroup messages={[message]} isLoading />,
      container,
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const step = container.querySelector<HTMLButtonElement>(
      "[data-testid='step']",
    );
    expect(step).not.toBeNull();
    // 参数不完整时不是一个可点的入口：不给手型
    expect(step!.className).not.toContain("cursor-pointer");
    step!.click();
    expect(h.artifacts.select).not.toHaveBeenCalled();
    expect(h.artifacts.setOpen).not.toHaveBeenCalled();
    // 半截 path 不显示
    expect(container.querySelector("[data-testid='result']")).toBeNull();
  });

  test("参数完整：点步骤按 query 编码选中对应的工具调用", async () => {
    const message = streamedMessage();
    expect(message.id).toBe("msg-1");
    root = renderInto(
      <MessageGroup messages={[message]} isLoading />,
      container,
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const step = container.querySelector<HTMLButtonElement>(
      "[data-testid='step']",
    )!;
    // 参数完整：是可点的入口
    expect(step.className).toContain("cursor-pointer");
    step.click();
    expect(h.artifacts.select).toHaveBeenCalledTimes(1);
    const url = new URL(h.artifacts.select.mock.calls[0]![0] as string);
    expect(url.searchParams.get("path")).toBe("/mnt/data/100% final#1?.md");
    expect(url.searchParams.get("tool_call_id")).toBe("call-1");
    expect(url.searchParams.get("message_id")).toBe("msg-1");
  });

  test("第二个片段不带 id / name（模型服务端的流就是这样）：SDK 按 index 归并，参数完整后照常可点", async () => {
    const cutInsidePath = rawArgs().indexOf("100% final") + 5;
    const full = rawArgs();
    const merged = new AIMessageChunk({
      id: "msg-1",
      content: "",
      tool_call_chunks: [
        {
          id: "call-1",
          name: "write_file",
          args: full.slice(0, cutInsidePath),
          index: 0,
          type: "tool_call_chunk",
        },
      ],
    }).concat(
      new AIMessageChunk({
        content: "",
        tool_call_chunks: [
          {
            args: full.slice(cutInsidePath),
            index: 0,
            type: "tool_call_chunk",
          },
        ],
      }),
    );
    const message = {
      ...merged.toDict().data,
      type: "ai",
    } as unknown as Message;
    // 前提：归并后的那一条 chunk 带着 id 与完整的原始串（不靠后续片段回填 id）
    const chunks = (
      message as unknown as {
        tool_call_chunks: { id?: string; args: string }[];
      }
    ).tool_call_chunks;
    expect(chunks).toHaveLength(1);
    expect(chunks[0]!.id).toBe("call-1");
    expect((JSON.parse(chunks[0]!.args) as { path: string }).path).toBe(
      "/mnt/data/100% final#1?.md",
    );
    root = renderInto(
      <MessageGroup messages={[message]} isLoading />,
      container,
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    container.querySelector<HTMLButtonElement>("[data-testid='step']")!.click();
    expect(h.artifacts.select).toHaveBeenCalledTimes(1);
    const url = new URL(h.artifacts.select.mock.calls[0]![0] as string);
    expect(url.searchParams.get("path")).toBe("/mnt/data/100% final#1?.md");
  });
});

describe("useArtifactContent：run 尚未 onFinish 时 pending → ready / failed", () => {
  let container: HTMLDivElement;
  let root: Root | null = null;
  let client: QueryClient;
  const seen: string[] = [];
  const fetchMock = vi.fn<typeof fetch>();

  function Probe({ url, enabled }: { url: string; enabled?: boolean }) {
    const { writeFileState } = useArtifactContentForTest({
      filepath: url,
      threadId: "thread-1",
      enabled,
    });
    useEffect(() => {
      if (writeFileState) seen.push(writeFileState.status);
    });
    return <output data-testid="state">{writeFileState?.status}</output>;
  }

  const REF =
    "write-file:?path=%2Fmnt%2Fdata%2Fa.md&message_id=msg-1&tool_call_id=call-1";
  const CALL: Message = {
    id: "msg-1",
    type: "ai",
    content: "",
    tool_calls: [
      {
        id: "call-1",
        name: "write_file",
        args: { path: "/mnt/data/a.md", content: "正文" },
      },
    ],
  } as unknown as Message;
  const RESULT: Message = {
    id: "tool-1",
    type: "tool",
    tool_call_id: "call-1",
    name: "write_file",
    content: "OK",
  } as unknown as Message;
  const ERROR_RESULT: Message = {
    ...RESULT,
    content: "Error: write_file content exceeds the limit",
  } as unknown as Message;

  function renderProbe() {
    root!.render(
      <QueryClientProvider client={client}>
        <Probe url={REF} />
      </QueryClientProvider>,
    );
  }

  async function settle() {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }

  /** SDK 每次流式更新都给一个新的 thread 对象：hook 据此重算归属信号、换 query key。 */
  async function streamUpdates(messages: Message[]) {
    await act(async () => {
      h.thread = { ...h.thread, messages };
      renderProbe();
    });
    await settle();
  }

  beforeEach(() => {
    seen.length = 0;
    fetchMock.mockReset();
    fetchMock.mockRejectedValue(new Error("fetch blocked by test"));
    vi.spyOn(globalThis, "fetch").mockImplementation(fetchMock);
    h.thread = { messages: [], isThreadLoading: false, isLoading: true };
    h.history = {
      isHistoryLoading: false,
      hasMoreHistory: false,
      refetchHistory: vi.fn(async () => undefined),
    };
    client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    root = null;
    container.remove();
    vi.restoreAllMocks();
  });

  test("参数流式完整、result 到位：pending → ready，不等 onFinish，不发 GET", async () => {
    await act(async () => {
      renderProbe();
    });
    await settle();
    expect(container.textContent).toContain("pending");

    // 参数完整了，result 还没到
    await streamUpdates([CALL]);
    expect(container.textContent).toContain("pending");
    // result 到位（流仍在进行）
    await streamUpdates([CALL, RESULT]);
    expect(container.textContent).toContain("ready");
    expect(h.thread.isLoading).toBe(true);
    expect(seen).toContain("pending");
    expect(seen.at(-1)).toBe("ready");
    // 非 append 的成功写入是 args.content 的内联快照；write-file 引用也不走普通文件的查询
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("工具 Error 到位：pending → failed，不发 GET", async () => {
    await act(async () => {
      renderProbe();
    });
    await settle();
    await streamUpdates([CALL, ERROR_RESULT]);
    expect(container.textContent).toContain("failed");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  const REPLACE_REF =
    "write-file:?path=%2Fmnt%2Fdata%2Fa.md&message_id=msg-2&tool_call_id=call-2";
  const REPLACE_MESSAGES = [
    {
      id: "msg-2",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "call-2",
          name: "str_replace",
          args: { path: "/mnt/data/a.md", old_str: "a", new_str: "b" },
        },
      ],
    },
    {
      id: "tool-2",
      type: "tool",
      tool_call_id: "call-2",
      name: "str_replace",
      content: "OK",
    },
  ] as unknown as Message[];

  test("enabled: false 时 write-file 引用也不加载：GET 不发，也不显示成「读取中」", async () => {
    h.thread = {
      messages: REPLACE_MESSAGES,
      isThreadLoading: false,
      isLoading: false,
    };
    const render = (enabled: boolean) => (
      <QueryClientProvider client={client}>
        <Probe url={REPLACE_REF} enabled={enabled} />
      </QueryClientProvider>
    );
    await act(async () => {
      root!.render(render(false));
    });
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();
    // 「暂不加载」不能泄漏成「等待中」
    expect(container.textContent).not.toContain("pending");

    // 放开之后才读当前文件
    // 每次调用给一个新的 Response（同一个实例的 body 只能读一次）
    fetchMock.mockImplementation(
      async () =>
        new Response("当前文件", { status: 200 }) as unknown as Response,
    );
    await act(async () => {
      root!.render(render(true));
    });
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("ready");
  });

  test("settled 之后加载标志变化不换 query key：str_replace 的当前文件只 GET 一次", async () => {
    // 每次调用给一个新的 Response（同一个实例的 body 只能读一次）
    fetchMock.mockImplementation(
      async () =>
        new Response("当前文件", { status: 200 }) as unknown as Response,
    );
    h.thread = {
      messages: REPLACE_MESSAGES,
      isThreadLoading: false,
      isLoading: true,
    };
    await act(async () => {
      root!.render(
        <QueryClientProvider client={client}>
          <Probe url={REPLACE_REF} />
        </QueryClientProvider>,
      );
    });
    await settle();
    expect(container.textContent).toContain("ready");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // 流结束：thread.isLoading 翻 false（SDK 每次更新都是新对象）——判定结果不变，不该再 GET
    await act(async () => {
      h.thread = { ...h.thread, isLoading: false };
      root!.render(
        <QueryClientProvider client={client}>
          <Probe url={REPLACE_REF} />
        </QueryClientProvider>,
      );
    });
    await settle();
    expect(container.textContent).toContain("ready");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("查询本身出错：显示 failed，不是一直 pending", async () => {
    h.thread = {
      messages: [CALL, RESULT] as unknown as Message[],
      isThreadLoading: false,
      isLoading: false,
    };
    await act(async () => {
      root!.render(
        <QueryClientProvider client={client}>
          <Probe url={REF} />
        </QueryClientProvider>,
      );
    });
    await settle();
    expect(container.textContent).toContain("ready");

    // loader 不该抛，但真抛了（或缓存里是错误态）不能被回退成永久的 pending
    const found = client
      .getQueryCache()
      .findAll({ queryKey: ["artifact", "write-file", REF] });
    expect(found).toHaveLength(1);
    await act(async () => {
      found[0]!.setState({
        ...found[0]!.state,
        status: "error",
        data: undefined,
        error: new Error("boom"),
        fetchStatus: "idle",
      });
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(container.textContent).toContain("failed");
  });
});
