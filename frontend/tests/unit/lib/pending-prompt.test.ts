import { describe, it, expect } from "vitest";

import { PENDING_PROMPT_KEY, readPendingPrompt } from "@/lib/pending-prompt";

/** Minimal Storage stub backed by a Map (vitest has no DOM localStorage/sessionStorage). */
function makeStorage(entries: Record<string, string> = {}): Storage {
  const store = new Map<string, string>(Object.entries(entries));
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => store.set(k, v),
    removeItem: (k: string) => store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => [...store.values()][i] ?? null,
    get length() {
      return store.size;
    },
  } as Storage;
}

describe("readPendingPrompt (C1a: fill chat input, no autosend)", () => {
  it("prefers the ?msg= URL param (short-prompt path)", () => {
    expect(readPendingPrompt("短提示", null)).toBe("短提示");
  });

  it("reads + consumes sessionStorage JSON for long prompts (no ?msg=)", () => {
    const long = "一段很长的脚本".repeat(500);
    const storage = makeStorage({
      [PENDING_PROMPT_KEY]: JSON.stringify({ prompt: long, autoSend: false }),
    });
    expect(readPendingPrompt(null, storage)).toBe(long);
    // consumed (removed) so it doesn't linger / re-fill on refresh
    expect(storage.getItem(PENDING_PROMPT_KEY)).toBeNull();
  });

  it("?msg= wins over sessionStorage when both are set", () => {
    const storage = makeStorage({
      [PENDING_PROMPT_KEY]: JSON.stringify({
        prompt: "from storage",
        autoSend: false,
      }),
    });
    expect(readPendingPrompt("from url", storage)).toBe("from url");
  });

  it("returns empty when neither ?msg= nor sessionStorage has a prompt", () => {
    expect(readPendingPrompt(null, null)).toBe("");
    expect(readPendingPrompt(null, makeStorage())).toBe("");
  });

  it("ignores malformed sessionStorage (returns empty, no throw)", () => {
    const storage = makeStorage({ [PENDING_PROMPT_KEY]: "{not json" });
    expect(readPendingPrompt(null, storage)).toBe("");
  });

  it("ignores sessionStorage entries whose prompt is not a string", () => {
    const storage = makeStorage({
      [PENDING_PROMPT_KEY]: JSON.stringify({ prompt: 123, autoSend: false }),
    });
    expect(readPendingPrompt(null, storage)).toBe("");
  });
});
