import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  skills: [] as { name: string; description: string; enabled: boolean }[],
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
  useSkills: () => ({ skills: h.skills, isLoading: false }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { InputBox } from "@/components/workspace/input-box";
import { ThreadContext } from "@/components/workspace/messages/context";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const fakeThread = { messages: [] } as never;

describe("InputBox new-thread suggestion visibility", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    document.cookie = "locale=zh-CN; path=/";
    h.skills = [
      { name: "ppt-master", description: "PPT", enabled: true },
      { name: "deep-research", description: "研究", enabled: true },
    ];
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderInput({
    showNewThreadSuggestions,
    agentSkills,
    threadId = "new",
    initialInput = "",
    isNewThread = true,
  }: {
    showNewThreadSuggestions?: boolean;
    agentSkills: string[];
    threadId?: string;
    initialInput?: string;
    isNewThread?: boolean;
  }) {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <ThreadContext.Provider value={{ thread: fakeThread }}>
            <PromptInputProvider initialInput={initialInput}>
              <InputBox
                threadId={threadId}
                isNewThread={isNewThread}
                context={{ model_name: "m1", mode: "flash" }}
                agentSkills={agentSkills}
                agentSuggestionPrompts={[
                  { label: "旧建议", prompt: "旧提示词" },
                ]}
                showNewThreadSuggestions={showNewThreadSuggestions}
              />
            </PromptInputProvider>
          </ThreadContext.Provider>
        </I18nProvider>,
      );
    });
  }

  it("removes dedicated skills, agent prompts and generic chips when disabled", async () => {
    await renderInput({
      showNewThreadSuggestions: false,
      agentSkills: ["ppt-master"],
    });

    expect(container.textContent).not.toContain("ppt-master");
    expect(container.textContent).not.toContain("旧建议");
    expect(container.textContent).not.toContain("小惊喜");
    expect(container.textContent).not.toContain("创建");
  });

  it("does not fall back to the global skill catalog when disabled", async () => {
    await renderInput({ showNewThreadSuggestions: false, agentSkills: [] });

    expect(container.textContent).not.toContain("deep-research");
    expect(container.textContent).not.toContain("小惊喜");
  });

  it("keeps dedicated skills and agent prompts at the default", async () => {
    await renderInput({ agentSkills: ["ppt-master"] });

    expect(container.textContent).toContain("ppt-master");
    expect(container.textContent).toContain("旧建议");
    expect(container.textContent).toContain("小惊喜");
  });

  it("keeps the global skill fallback for non-target agents at the default", async () => {
    await renderInput({ agentSkills: [] });

    expect(container.textContent).toContain("deep-research");
  });

  it("does not clear a new-session handoff when its provisional ID changes", async () => {
    await renderInput({
      agentSkills: [],
      threadId: "provisional-1",
      initialInput: "handoff",
      showNewThreadSuggestions: false,
    });
    expect(container.querySelector("textarea")?.value).toBe("handoff");

    await renderInput({
      agentSkills: [],
      threadId: "provisional-2",
      initialInput: "handoff",
      showNewThreadSuggestions: false,
    });
    expect(container.querySelector("textarea")?.value).toBe("handoff");
  });

  it("clears an existing-thread draft when entering a fresh session", async () => {
    await renderInput({
      agentSkills: [],
      threadId: "existing-thread",
      initialInput: "旧线程草稿",
      isNewThread: false,
      showNewThreadSuggestions: false,
    });
    expect(container.querySelector("textarea")?.value).toBe("旧线程草稿");

    await renderInput({
      agentSkills: [],
      threadId: "provisional-new",
      initialInput: "旧线程草稿",
      isNewThread: true,
      showNewThreadSuggestions: false,
    });
    expect(container.querySelector("textarea")?.value).toBe("");
  });
});
