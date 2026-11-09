/**
 * Full-draft modal tests (Tasks W2 + W10).
 *
 * W2 — light/heavy diversion pure helpers:
 * - pickFullDraftChannel: any data_source checked → heavy, else light.
 * - buildAssistRequestForFullDraft: action → AssistRequest shape mapping
 *   (polish/expand/shorten all use the op-based branch — op: action).
 *
 * W10 — full-text 润色/精简/扩写 diff preview + long-doc size guard:
 * - shouldShowFullDraftDiff: under threshold → show diff; over → skip (the
 *   O(n·m) computeDiff could blow up on very long docs; guard sidesteps it).
 * - pickFullDraftApplyCallback: the wholesale setContent only fires after the
 *   user confirms in the preview state (not auto on stream end).
 * - W8 infra reuse: the modal imports DiffView from W8's diff-view module
 *   (regression guard against reimplementation).
 *
 * The modal's React rendering isn't driven here (DOM-free; the branching
 * logic lives in the pure helpers, which are the testable surface).
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import {
  buildAssistRequestForFullDraft,
  FULL_DRAFT_DIFF_MAX_CELL_PRODUCT,
  pickFullDraftApplyCallback,
  pickFullDraftChannel,
  shouldShowFullDraftDiff,
  stripCodeFences,
} from "@/components/workspace/writer/full-draft-modal";

// ── W2: pickFullDraftChannel ──────────────────────────────────────────────

test("pickFullDraftChannel returns light when no data source is checked", () => {
  const empty = new Set<"knowledge" | "web">();
  expect(pickFullDraftChannel("polish", empty)).toBe("light");
});

test("pickFullDraftChannel returns heavy when web is checked", () => {
  const webOnly = new Set<"knowledge" | "web">(["web"]);
  expect(pickFullDraftChannel("polish", webOnly)).toBe("heavy");
});

test("pickFullDraftChannel returns heavy when knowledge is checked (future-proof)", () => {
  const knowledgeOnly = new Set<"knowledge" | "web">(["knowledge"]);
  expect(pickFullDraftChannel("expand", knowledgeOnly)).toBe("heavy");
});

// ── W2: buildAssistRequestForFullDraft ─────────────────────────────────────

test("buildAssistRequestForFullDraft maps polish to op:polish with doc_type", () => {
  const req = buildAssistRequestForFullDraft("polish", "# Hello", "course");
  expect(req.op).toBe("polish");
  expect(req.text).toBe("# Hello");
  expect(req.doc_type).toBe("course");
  expect(req.instruction).toBeUndefined();
});

test("buildAssistRequestForFullDraft maps expand to op:expand with doc_type", () => {
  const req = buildAssistRequestForFullDraft("expand", "# Hello", "article");
  expect(req.op).toBe("expand");
  expect(req.text).toBe("# Hello");
  expect(req.doc_type).toBe("article");
  expect(req.instruction).toBeUndefined();
  // full-draft sends the entire document; full_doc lifts the backend's
  // 4000-char input limit + 2000-token output cap (assist_service.py).
  expect(req.full_doc).toBe(true);
});

test("buildAssistRequestForFullDraft maps shorten to op:shorten (op-based, no dummy)", () => {
  const req = buildAssistRequestForFullDraft("shorten", "# Hello", "general");
  expect(req.op).toBe("shorten");
  expect(req.text).toBe("# Hello");
  expect(req.doc_type).toBe("general");
  // The dummy op:"summarize" and instruction are both gone —
  // shorten is now op-based, consistent with polish/expand.
  expect(req.instruction).toBeUndefined();
  // full-draft sends the entire document; full_doc lifts the backend's
  // 4000-char input limit + 2000-token output cap (assist_service.py).
  expect(req.full_doc).toBe(true);
});

test("buildAssistRequestForFullDraft omits doc_type when contentType is undefined", () => {
  const req = buildAssistRequestForFullDraft("polish", "# Hello");
  expect(req.doc_type).toBeUndefined();
});

// ── W2: full_doc flag (lifts backend input/output limits for whole-document edits) ─

test("buildAssistRequestForFullDraft sets full_doc=true for polish (op branch)", () => {
  const req = buildAssistRequestForFullDraft("polish", "# Hello", "course");
  expect(req.full_doc).toBe(true);
});

test("buildAssistRequestForFullDraft sets full_doc=true for shorten (op branch)", () => {
  const req = buildAssistRequestForFullDraft("shorten", "# Hello", "general");
  expect(req.full_doc).toBe(true);
});

// ── P1-10: shouldShowFullDraftDiff (N×M cell-product guard) ────────────────
//
// computeDiff is O(n·m) LCS DP on token count (CJK ≈ 1 token/char). The old
// guard checked only original length, so a 6000-char doc expanded 2× built a
// 72M-cell matrix (main-thread freeze + hundreds of MB). The gate now checks
// the cell product origLen × revisedLen against FULL_DRAFT_DIFF_MAX_CELL_PRODUCT.

test("shouldShowFullDraftDiff: small docs → diff shown", () => {
  expect(shouldShowFullDraftDiff(1000, 1500)).toBe(true); // 1.5M cells
});

test("shouldShowFullDraftDiff: long-doc expand → diff skipped (P1-10)", () => {
  // 6000 × 12000 = 72M cells → must skip to avoid main-thread freeze
  expect(shouldShowFullDraftDiff(6000, 12000)).toBe(false);
});

test("shouldShowFullDraftDiff: boundary at the cell-product threshold", () => {
  // exactly FULL_DRAFT_DIFF_MAX_CELL_PRODUCT (25M, Fix 1.3) → inclusive → shown
  const root = Math.floor(Math.sqrt(FULL_DRAFT_DIFF_MAX_CELL_PRODUCT));
  expect(shouldShowFullDraftDiff(root, root)).toBe(true);
  // one cell over → skipped
  expect(shouldShowFullDraftDiff(root + 1, root)).toBe(false);
});

test("shouldShowFullDraftDiff honors a custom max product", () => {
  expect(shouldShowFullDraftDiff(500, 400, 300_000)).toBe(true); // 200k ≤ 300k
  expect(shouldShowFullDraftDiff(500, 400, 100_000)).toBe(false); // 200k > 100k
});

// ── W10: pickFullDraftApplyCallback (preview gate — no auto-apply) ──────────
//
// The wholesale setContent (handleFullDraftApply → setContentViaEditor) must
// only fire after the user confirms in the preview state, NOT automatically
// when the stream ends. This pure seam makes that gate testable without
// @testing-library/react: it returns null while streaming (no auto-apply)
// and a confirm thunk only in the preview phase.

test("pickFullDraftApplyCallback returns null while streaming (no auto-apply on stream end)", () => {
  const onApply = vi.fn();
  expect(
    pickFullDraftApplyCallback("streaming", "结果文本", onApply),
  ).toBeNull();
  expect(pickFullDraftApplyCallback("idle", "结果文本", onApply)).toBeNull();
  expect(onApply).not.toHaveBeenCalled();
});

test("pickFullDraftApplyCallback returns a confirm thunk in preview that calls onApply with the result", () => {
  const onApply = vi.fn();
  const thunk = pickFullDraftApplyCallback("preview", "结果文本", onApply);
  expect(thunk).toBeTypeOf("function");
  thunk!();
  expect(onApply).toHaveBeenCalledWith("结果文本");
});

test("pickFullDraftApplyCallback returns null when the preview result is empty", () => {
  const onApply = vi.fn();
  expect(pickFullDraftApplyCallback("preview", "", onApply)).toBeNull();
  expect(onApply).not.toHaveBeenCalled();
});

// ── W10: reuses W8 diff infra (no reimplementation) ────────────────────────

test("full-draft-modal reuses W8 DiffView from the diff-view module (no reimplementation)", () => {
  // Resolve the modal source via the same `@` alias vitest uses (`@` → src).
  // import.meta.url points at this test file; we walk up to the repo frontend
  // root and into src/ so the assertion survives path-alias transforms.
  const here = fileURLToPath(new URL(".", import.meta.url));
  const modalSrc = readFileSync(
    resolve(
      here,
      "../../../../../src/components/workspace/writer/full-draft-modal.tsx",
    ),
    "utf-8",
  );

  // Imports the W8 DiffView (which wraps computeDiff internally) — reuse,
  // not a reimplementation.
  expect(modalSrc).toContain('from "./diff-view"');
  expect(modalSrc).toMatch(/DiffView/);
  // No local reimplementation of the diff algorithm/view.
  expect(modalSrc).not.toMatch(/function\s+computeDiff/);
  expect(modalSrc).not.toMatch(/function\s+DiffView/);
});

// ── W22: citation list rendering (heavy/web path) ──────────────────────────
//
// The heavy path (web checked → co-writer agent) may receive a trailing
// `[[CITATIONS:<json>]]` marker from the co-writer (SOUL §8.6). The modal
// must call parseCitations to split the marker from the body and render a
// citation list below the result. The pure helper is unit-tested in
// citations.test.ts; this is a source-reading test (same pattern as the W8
// DiffView test above) verifying the modal wires the helper in.

test("full-draft-modal imports parseCitations from citations module (W22)", () => {
  const here = fileURLToPath(new URL(".", import.meta.url));
  const modalSrc = readFileSync(
    resolve(
      here,
      "../../../../../src/components/workspace/writer/full-draft-modal.tsx",
    ),
    "utf-8",
  );

  // Imports the W22 parseCitations helper (not a local reimplementation).
  expect(modalSrc).toContain("parseCitations");
  expect(modalSrc).toMatch(/from ["']@\/core\/writer\/citations["']/);
  // No local reimplementation of the citation parser.
  expect(modalSrc).not.toMatch(/function\s+parseCitations/);
});

// ── Fix 1.4: stripCodeFences (defensive — weak models wrap output in fences) ──

test("stripCodeFences unwraps a ```markdown wrapper", () => {
  expect(stripCodeFences("```markdown\n# 标题\n正文\n```")).toBe(
    "# 标题\n正文",
  );
});

test("stripCodeFences unwraps a bare ``` wrapper", () => {
  expect(stripCodeFences("```\ncontent\n```")).toBe("content");
});

test("stripCodeFences leaves non-fenced text unchanged", () => {
  expect(stripCodeFences("# 标题\n正文")).toBe("# 标题\n正文");
});

test("stripCodeFences does not touch mid-text inline code blocks", () => {
  const mid = "说明\n```code\nx\n```\n结尾";
  expect(stripCodeFences(mid)).toBe(mid);
});
