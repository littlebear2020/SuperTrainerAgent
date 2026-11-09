import { describe, expect, it, vi } from "vitest";

import { submitFileVarsCase } from "@/core/gallery/file-vars-submit";

const file = (name: string) => ({ name }) as File;

function makeDeps() {
  const calls: string[] = [];
  return {
    calls,
    deps: {
      instantiate: vi.fn(
        async (caseId: string, _vars: Record<string, string>) => {
          calls.push(`instantiate:${caseId}`);
          return { thread_id: "t-1", compiled_prompt: "请分析文档" };
        },
      ),
      upload: vi.fn(async (threadId: string, files: File[]) => {
        calls.push(`upload:${threadId}:${files.map((f) => f.name).join(",")}`);
      }),
      seedPrompt: vi.fn(
        (payload: {
          prompt: string;
          autoSend: boolean;
          existingThread: boolean;
        }) => {
          calls.push(
            `seed:${payload.prompt}:${payload.autoSend}:${payload.existingThread}`,
          );
        },
      ),
      navigate: vi.fn((threadId: string) => {
        calls.push(`nav:${threadId}`);
      }),
    },
  };
}

describe("submitFileVarsCase", () => {
  it("按 建线程→上传→预填→跳转 顺序执行，文件被扁平化", async () => {
    const { calls, deps } = makeDeps();
    await submitFileVarsCase(
      "case-1",
      { topic: "微课" },
      { doc: [file("a.docx")], extra: [file("b.pdf")] },
      deps,
    );
    expect(calls).toEqual([
      "instantiate:case-1",
      "upload:t-1:a.docx,b.pdf",
      "seed:请分析文档:true:true",
      "nav:t-1",
    ]);
  });

  it("无文件时跳过上传", async () => {
    const { calls, deps } = makeDeps();
    await submitFileVarsCase("case-1", {}, {}, deps);
    expect(calls).toEqual([
      "instantiate:case-1",
      "seed:请分析文档:true:true",
      "nav:t-1",
    ]);
  });

  it("上传失败 → 抛错且不跳转", async () => {
    const { calls, deps } = makeDeps();
    deps.upload.mockRejectedValueOnce(new Error("网络错误"));
    await expect(
      submitFileVarsCase("case-1", {}, { doc: [file("a.docx")] }, deps),
    ).rejects.toThrow("网络错误");
    expect(calls).toEqual(["instantiate:case-1"]);
  });
});
