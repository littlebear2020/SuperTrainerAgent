import { describe, expect, it } from "vitest";

import { resolveReplyPayload } from "@/components/workspace/writer/from-scratch-panel";

/**
 * P2-15: handleReply ignored its argument and read replyInput, so clicking a
 * clarify option with an empty input did nothing. resolveReplyPayload prefers
 * the passed option text and falls back to the input box.
 */
describe("resolveReplyPayload (P2-15)", () => {
  it("uses the option text when provided", () => {
    expect(resolveReplyPayload("选项 A", "")).toBe("选项 A");
  });

  it("falls back to replyInput when no option text", () => {
    expect(resolveReplyPayload(undefined, "手动输入")).toBe("手动输入");
  });

  it("option text wins over replyInput", () => {
    expect(resolveReplyPayload("选项 A", "手动输入")).toBe("选项 A");
  });

  it("returns null when both empty/whitespace", () => {
    expect(resolveReplyPayload(undefined, "   ")).toBeNull();
    expect(resolveReplyPayload(undefined)).toBeNull();
    expect(resolveReplyPayload("")).toBeNull();
  });
});
