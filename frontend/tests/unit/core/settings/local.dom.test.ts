import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  applyThreadModelOverride,
  getLocalSettings,
  getThreadModelName,
  LOCAL_SETTINGS_KEY,
  saveThreadModelName,
} from "@/core/settings/local";

const storage = new Map<string, string>();

beforeEach(() => {
  storage.clear();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => void storage.set(key, value),
    removeItem: (key: string) => void storage.delete(key),
    clear: () => void storage.clear(),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it.each(["pro", "thinking"])(
  "migrates legacy %s mode and clears persisted effort without dropping thread model override",
  (mode) => {
    localStorage.setItem(
      LOCAL_SETTINGS_KEY,
      JSON.stringify({
        context: {
          mode,
          model_name: "deepseek-v4.1-flash",
          reasoning_effort: "low",
        },
      }),
    );
    saveThreadModelName("thread-1", "claude-sonnet-5-5");

    const settings = getLocalSettings();
    const overridden = applyThreadModelOverride(
      settings,
      getThreadModelName("thread-1"),
    );

    expect(settings.context.mode).toBe("standard");
    expect(settings.context.model_name).toBe("deepseek-v4.1-flash");
    expect("reasoning_effort" in settings.context).toBe(false);
    expect(overridden.context.model_name).toBe("claude-sonnet-5-5");
    expect(overridden.context.mode).toBe("standard");
    expect("reasoning_effort" in overridden.context).toBe(false);
  },
);
