import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "vitest";

import { loadArtifactContentFromToolCall } from "@/core/artifacts/loader";
import {
  completeToolArgs,
  createWriteFileRef,
  isCompleteToolArgs,
  isWriteFileToolCall,
  parseWriteFileRef,
  rawArgsByToolCallId,
} from "@/core/artifacts/write-file-ref";

/**
 * PR4（2026-11-24 前端可靠性批）：write-file 引用把 path 放进 URL pathname，
 * `%` / `#` / `?` 一类字符会被截断或让 decodeURIComponent 抛错。新格式把 path 作为
 * 独立的 query 参数编码；旧格式里合法的固定 fixture 仍可解析，截断 / 非法的返回 null。
 */

const IDS = { messageId: "msg-1", toolCallId: "call-1" };

describe("createWriteFileRef / parseWriteFileRef", () => {
  it.each([
    "/mnt/data/100%.txt",
    "/mnt/data/report#final.txt",
    "/mnt/data/what?.txt",
    "/mnt/data/中文 文件.txt",
    "/mnt/data/a+b.txt",
    "/mnt/data/plain.md",
  ])("新格式 round-trip：%s", (path) => {
    const ref = createWriteFileRef({ path, ...IDS });
    expect(ref).not.toBeNull();
    expect(ref).toMatch(/^write-file:\?/);
    // path 不再出现在 pathname 里
    expect(new URL(ref!).pathname).toBe("");
    expect(parseWriteFileRef(ref!)).toEqual({ path, ...IDS });
  });

  it("合法的旧格式（path 在 pathname）仍可解析，只解码一次", () => {
    expect(
      parseWriteFileRef(
        "write-file:/mnt/data/report.txt?message_id=msg-1&tool_call_id=call-1",
      ),
    ).toEqual({ path: "/mnt/data/report.txt", ...IDS });
    expect(
      parseWriteFileRef(
        "write-file:/mnt/data/%E4%B8%AD%E6%96%87.txt?message_id=msg-1&tool_call_id=call-1",
      ),
    ).toEqual({ path: "/mnt/data/中文.txt", ...IDS });
  });

  it("旧格式里被截断或非法的值返回 null，不抛异常", () => {
    expect(
      parseWriteFileRef(
        "write-file:/mnt/data/100%.txt?message_id=msg-1&tool_call_id=call-1",
      ),
    ).toBeNull();
    // `#` 把后面的内容切进 fragment：定位字段丢失
    expect(
      parseWriteFileRef(
        "write-file:/mnt/data/report#final.txt?message_id=msg-1&tool_call_id=call-1",
      ),
    ).toBeNull();
    // `?` 把后面的内容切进 query：path 被截断成目录前缀，定位字段对不上
    expect(
      parseWriteFileRef(
        "write-file:/mnt/data/what?.txt?message_id=msg-1&tool_call_id=call-1",
      ),
    ).toBeNull();
    expect(() =>
      parseWriteFileRef(
        "write-file:/mnt/data/100%.txt?message_id=msg-1&tool_call_id=call-1",
      ),
    ).not.toThrow();
  });

  it("新格式里的非法 percent 编码返回 null，不二次解码", () => {
    expect(
      parseWriteFileRef(
        "write-file:?path=%2G&message_id=msg-1&tool_call_id=call-1",
      ),
    ).toBeNull();
    // %2520 只解码一次：得到字面的 %20，而不是空格
    expect(
      parseWriteFileRef(
        "write-file:?path=%2520&message_id=msg-1&tool_call_id=call-1",
      ),
    ).toEqual({ path: "%20", ...IDS });
  });

  it("query 的键被 percent 编码（p%61th）：与 URLSearchParams 同样解码后按新格式解析", () => {
    expect(
      parseWriteFileRef(
        "write-file:?p%61th=%2Fmnt%2Fdata%2Fa.md&message_id=m1&tool_call_id=call-1",
      ),
    ).toEqual({
      path: "/mnt/data/a.md",
      messageId: "m1",
      toolCallId: "call-1",
    });
  });

  it("缺 path / messageId / toolCallId 任一项都不生成引用；解析缺字段返回 null", () => {
    expect(createWriteFileRef({ path: "", ...IDS })).toBeNull();
    expect(
      createWriteFileRef({
        path: "/mnt/data/a.txt",
        messageId: "",
        toolCallId: IDS.toolCallId,
      }),
    ).toBeNull();
    expect(
      createWriteFileRef({
        path: "/mnt/data/a.txt",
        messageId: "msg-1",
        toolCallId: "",
      }),
    ).toBeNull();
    expect(
      parseWriteFileRef(
        "write-file:?path=%2Fmnt%2Fdata%2Fa.txt&message_id=msg-1",
      ),
    ).toBeNull();
    expect(
      parseWriteFileRef("write-file:?path=%2Fmnt%2Fdata%2Fa.txt"),
    ).toBeNull();
    expect(parseWriteFileRef("write-file:")).toBeNull();
    expect(parseWriteFileRef("/mnt/data/a.txt")).toBeNull();
    expect(
      parseWriteFileRef(
        "write-file:?path=%2Fmnt%2Fdata%2Fa.txt&tool_call_id=call-1",
      ),
    ).toBeNull();
  });
});

describe("loadArtifactContentFromToolCall：定位与校验（五态里的 ready / invalid）", () => {
  const aiWithCall = (
    id: string,
    toolCallId: string,
    path: string,
    content: string,
  ): Message =>
    ({
      id,
      type: "ai",
      content: "",
      tool_calls: [
        { id: toolCallId, name: "write_file", args: { path, content } },
      ],
    }) as unknown as Message;

  const toolOk = (toolCallId: string): Message =>
    ({
      id: `tool-${toolCallId}`,
      type: "tool",
      tool_call_id: toolCallId,
      name: "write_file",
      content: "OK",
    }) as unknown as Message;

  const thread = (messages: Message[]) =>
    ({ messages }) as unknown as Parameters<
      typeof loadArtifactContentFromToolCall
    >[0]["thread"];

  async function load(url: string, t: ReturnType<typeof thread>) {
    return loadArtifactContentFromToolCall({ url, thread: t, threadId: "t-1" });
  }

  it("messageId 重复时以 toolCallId 找到正确的消息", async () => {
    const t = thread([
      aiWithCall("dup", "call-a", "/mnt/data/a.txt", "A 的正文"),
      aiWithCall("dup", "call-b", "/mnt/data/b.txt", "B 的正文"),
      toolOk("call-a"),
      toolOk("call-b"),
    ]);
    const ref = createWriteFileRef({
      path: "/mnt/data/b.txt",
      messageId: "dup",
      toolCallId: "call-b",
    });
    await expect(load(ref!, t)).resolves.toEqual({
      status: "ready",
      content: "B 的正文",
    });
  });

  it("path 与定位到的 tool call 不一致、引用非法或缺字段时是 invalid", async () => {
    const t = thread([
      aiWithCall("m1", "call-1", "/mnt/data/a.txt", "正文"),
      toolOk("call-1"),
    ]);
    const mismatch = createWriteFileRef({
      path: "/mnt/data/other.txt",
      messageId: "m1",
      toolCallId: "call-1",
    });
    await expect(load(mismatch!, t)).resolves.toEqual({ status: "invalid" });
    await expect(load("write-file:/100%.txt", t)).resolves.toEqual({
      status: "invalid",
    });
    await expect(
      load("write-file:?path=%2Fmnt%2Fdata%2Fa.txt", t),
    ).resolves.toEqual({ status: "invalid" });
  });
});

describe("流式工具参数：UI 与内容加载共用的「参数是否完整」判定", () => {
  it("严格解析：JSON 对象才算完整，半截串与数组都不算", () => {
    expect(isCompleteToolArgs('{"path":"/a"}')).toBe(true);
    expect(isCompleteToolArgs('{"path":"/a')).toBe(false);
    expect(isCompleteToolArgs("[1,2]")).toBe(false);
    expect(isCompleteToolArgs("")).toBe(false);
  });

  it("工具步骤用的参数：流式未完整 → 空对象；result 已到或没有原始串 → 直接用 args", () => {
    const args = { path: "/a", content: "x" };
    expect(completeToolArgs(args, '{"path":"/a"', false)).toEqual({});
    expect(completeToolArgs(args, '{"path":"/a"', true)).toEqual(args);
    expect(completeToolArgs(args, undefined, false)).toEqual(args);
    expect(completeToolArgs(args, '{"path":"/b"}', false)).toEqual({
      path: "/b",
    });
  });

  it("原始串按 id 归并：没有 id / args 不是字符串的片段不计，没有 chunk 的消息为空", () => {
    const message = {
      type: "ai",
      id: "m1",
      content: "",
      tool_call_chunks: [
        { id: "c1", name: "write_file", args: '{"a":' },
        { id: "c1", args: "1}" },
        { args: "ignored" },
        { id: "c2", args: 7 },
      ],
    } as unknown as Message;
    expect(rawArgsByToolCallId(message).get("c1")).toBe('{"a":1}');
    expect(rawArgsByToolCallId(message).has("c2")).toBe(false);
    expect(
      rawArgsByToolCallId({
        type: "ai",
        id: "m2",
        content: "",
      } as unknown as Message).size,
    ).toBe(0);
    expect(
      rawArgsByToolCallId({
        type: "tool",
        id: "t",
        tool_call_id: "c1",
        name: "write_file",
        content: "",
      } as unknown as Message).size,
    ).toBe(0);
  });

  it("write_file / str_replace 是需要引用定位的两类工具", () => {
    expect(isWriteFileToolCall("write_file")).toBe(true);
    expect(isWriteFileToolCall("str_replace")).toBe(true);
    expect(isWriteFileToolCall("read_file")).toBe(false);
    expect(isWriteFileToolCall(undefined)).toBe(false);
  });
});
