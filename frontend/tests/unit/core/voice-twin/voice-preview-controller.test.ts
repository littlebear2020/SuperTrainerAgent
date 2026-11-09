import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createVoicePreviewController } from "@/core/voice-twin/voice-preview-controller";

describe("VoicePreviewController", () => {
  let audioEl: {
    play: ReturnType<typeof vi.fn>;
    pause: ReturnType<typeof vi.fn>;
    src: string;
    onended: (() => void) | null;
    onerror: (() => void) | null;
  };
  let playSpy: ReturnType<typeof vi.fn>;
  let pauseSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    playSpy = vi.fn(() => Promise.resolve());
    pauseSpy = vi.fn();
    audioEl = {
      play: playSpy,
      pause: pauseSpy,
      src: "",
      onended: null,
      onerror: null,
    };
    vi.stubGlobal(
      "Audio",
      vi.fn(function () {
        return audioEl;
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("toggle starts a voice: marks both playing and loading", () => {
    const c = createVoicePreviewController();
    expect(c.toggle("vt-1", "u")).toBe("vt-1");
    expect(c.getPlayingId()).toBe("vt-1");
    expect(c.getLoadingId()).toBe("vt-1");
    expect(playSpy).toHaveBeenCalledTimes(1);
  });

  it("toggle the playing voice again pauses it (returns null)", () => {
    const c = createVoicePreviewController();
    c.toggle("vt-1", "u");
    expect(c.toggle("vt-1", "u")).toBeNull();
    expect(c.getPlayingId()).toBeNull();
    expect(pauseSpy).toHaveBeenCalled();
  });

  it("switching voices stops the previous and plays the new", () => {
    const c = createVoicePreviewController();
    c.toggle("vt-1", "a");
    c.toggle("vt-2", "b");
    expect(c.getPlayingId()).toBe("vt-2");
    expect(pauseSpy).toHaveBeenCalled();
    expect(playSpy).toHaveBeenCalledTimes(2);
  });

  it("loading clears once the play() promise resolves, playing continues", async () => {
    const c = createVoicePreviewController();
    c.toggle("vt-1", "u");
    expect(c.getLoadingId()).toBe("vt-1");
    // vi.waitFor polls until the play().then(...) chain settles — no coupling
    // to the controller's exact promise-chain depth (2026-10-05 review).
    await vi.waitFor(() => {
      expect(c.getLoadingId()).toBeNull();
    });
    expect(c.getPlayingId()).toBe("vt-1");
  });

  it("on audio end, playing resets to null", () => {
    const c = createVoicePreviewController();
    c.toggle("vt-1", "u");
    expect(c.getPlayingId()).toBe("vt-1");
    audioEl.onended?.();
    expect(c.getPlayingId()).toBeNull();
  });

  it("onChange fires whenever playing/loading changes", () => {
    const onChange = vi.fn();
    const c = createVoicePreviewController({ onChange });
    c.toggle("vt-1", "u");
    expect(onChange).toHaveBeenCalled();
  });

  it("switching voices during a failing play keeps the new voice's state", async () => {
    // First play() rejects (mimics the AbortError a browser raises when
    // pause()/src-change interrupts an unsettled play()); second resolves
    // (the default from beforeEach). The rejected first voice's .catch()
    // must NOT clobber the new voice's state.
    playSpy.mockReturnValueOnce(Promise.reject(new Error("interrupted")));

    const c = createVoicePreviewController();
    c.toggle("vt-1", "a");
    c.toggle("vt-2", "b");
    expect(c.getPlayingId()).toBe("vt-2");

    // Regression: vt-1's .catch() used to null out playingId even though
    // vt-2 now owns playback, desyncing the UI (Play icon instead of Pause).
    // vi.waitFor polls until vt-1's rejected .catch() and vt-2's resolved
    // .then() have both run — no coupling to the exact promise-chain depth
    // (2026-10-05 review).
    await vi.waitFor(() => {
      expect(c.getLoadingId()).toBeNull(); // vt-2's play() resolved
      expect(c.getPlayingId()).toBe("vt-2");
    });
  });
});
