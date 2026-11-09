import { afterEach, describe, expect, it, vi } from "vitest";

import { streamAgentEdit } from "@/core/writer/api";

describe("streamAgentEdit abort (P1-9)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("passes opts.signal through to fetch (heavy channel becomes cancellable)", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: {
        getReader: () => ({
          read: vi.fn().mockResolvedValue({ done: true, value: undefined }),
          cancel: vi.fn(),
        }),
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const gen = streamAgentEdit({
      instruction: "改写",
      markdown: "# hi",
      mode: "polish",
      signal: controller.signal,
    });
    // Drain the generator: the first next() only reaches the "status" yield —
    // fetch is issued further into the body (执行中修订：原计划单 next() 不发
    // fetch 的注释不准）。
    let drained = false;
    while (!drained) {
      drained = (await gen.next()).done ?? true;
    }
    expect(fetchMock).toHaveBeenCalled();
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(init.signal).toBe(controller.signal);
  });
});
