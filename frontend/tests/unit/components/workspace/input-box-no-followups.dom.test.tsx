import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  fetch: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light" }),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
  getLangGraphBaseURL: () => "",
}));

vi.mock("@/core/api/fetcher", () => ({
  fetch: h.fetch,
}));

vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({
    models: [
      {
        name: "m1",
        display_name: "模型一",
        model: "m1",
        supports_thinking: false,
      },
    ],
    tokenUsageEnabled: false,
    isLoading: false,
  }),
}));

vi.mock("@/core/skills/hooks", () => ({
  useSkills: () => ({ skills: [], isLoading: false }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { InputBox } from "@/components/workspace/input-box";
import { ThreadContext } from "@/components/workspace/messages/context";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const fakeThread = {
  messages: [
    { id: "h1", type: "human", content: "继续" },
    { id: "a1", type: "ai", content: "已完成" },
  ],
} as never;

describe("InputBox dynamic follow-up removal", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    document.cookie = "locale=zh-CN; path=/";
    h.fetch.mockReset();
    h.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ suggestions: ["追问一"] }),
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderStatus(status: "streaming" | "ready") {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <ThreadContext.Provider value={{ thread: fakeThread }}>
            <PromptInputProvider>
              <InputBox
                threadId="thread-1"
                isNewThread={false}
                status={status}
                context={{ model_name: "m1", mode: "flash" }}
              />
            </PromptInputProvider>
          </ThreadContext.Provider>
        </I18nProvider>,
      );
    });
  }

  it("does not request suggestions after streaming becomes ready", async () => {
    await renderStatus("streaming");
    await renderStatus("ready");

    const suggestionCalls = h.fetch.mock.calls.filter(([url]) =>
      String(url).includes("/suggestions"),
    );
    expect(suggestionCalls).toHaveLength(0);
  });
});
