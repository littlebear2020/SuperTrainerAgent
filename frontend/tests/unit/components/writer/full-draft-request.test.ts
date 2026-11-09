import { describe, expect, it } from "vitest";

import { buildAssistRequestForFullDraft } from "@/components/workspace/writer/full-draft-modal";

/**
 * P1-8: the full-draft light path used to accept _instruction but drop it
 * (comment "deferred"), silently ignoring the user's custom directive even
 * though the UI textarea was editable. Backend _build_prompt already
 * supports instruction — forwarding is a one-line fix.
 */
describe("buildAssistRequestForFullDraft (P1-8)", () => {
  it("透传用户自定义 instruction", () => {
    const req = buildAssistRequestForFullDraft(
      "polish",
      "# 标题\n正文",
      "article",
      "保留所有小标题，只改口语化",
    );
    expect(req.instruction).toBe("保留所有小标题，只改口语化");
    expect(req.full_doc).toBe(true);
    expect(req.op).toBe("polish");
  });

  it("无 instruction 时不设该字段", () => {
    const req = buildAssistRequestForFullDraft("expand", "正文", "general");
    expect(req.instruction).toBeUndefined();
    expect(req.text).toBe("正文");
  });
});
