import { describe, expect, it } from "vitest";

import { buildFollowUpAssistRequest } from "@/components/workspace/writer/selection-popover";

/**
 * P0-2 contract: the follow-up refine request must keep `instruction` to the
 * user's directive only. The call site used to embed the full streamedText
 * into instruction (also passed as `text`) → >1000 chars 422 + duplicated
 * text in the prompt. These tests lock the request-builder contract so the
 * call site can never regress to embedding the body into instruction.
 */
describe("buildFollowUpAssistRequest (P0-2)", () => {
  it("instruction 只承载用户要求，不内嵌 streamedText（防 1000 字 422）", () => {
    const longText = "x".repeat(2000); // AI 生成的长结果
    const req = buildFollowUpAssistRequest(
      "rewrite",
      "改得更口语化",
      longText,
      "article",
      undefined,
    );
    expect(req.instruction).toBe("改得更口语化");
    expect(req.text).toBe(longText);
    expect((req.instruction ?? "").length).toBeLessThanOrEqual(1000);
    // instruction 绝不包含 streamedText 内容
    expect(req.instruction).not.toContain("xxxx");
  });

  it("text 承载完整 streamedText，full_doc=true", () => {
    const req = buildFollowUpAssistRequest(
      "polish",
      "润色",
      "正文内容",
      "general",
    );
    expect(req.text).toBe("正文内容");
    expect(req.full_doc).toBe(true);
    expect(req.op).toBe("polish");
  });
});
