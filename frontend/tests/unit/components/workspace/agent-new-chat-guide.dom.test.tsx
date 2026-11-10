import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PromptInputProvider,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";
import { AgentNewChatGuide } from "@/components/workspace/agent-new-chat-guide";
import type { AgentNewChatGuideId } from "@/core/agents/new-chat";
import { I18nProvider, useI18nContext } from "@/core/i18n/context";
import { enUS, zhCN } from "@/core/i18n/locales";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function TestComposer() {
  const { textInput } = usePromptInputController();
  return (
    <textarea
      data-testid="composer"
      name="message"
      value={textInput.value}
      readOnly
    />
  );
}

function LocaleSwitch() {
  const { setLocale } = useI18nContext();
  return (
    <button
      type="button"
      data-testid="locale-switch"
      onClick={() => setLocale("en-US")}
    >
      English
    </button>
  );
}

describe("AgentNewChatGuide", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    document.cookie = "locale=zh-CN; path=/";
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderGuide({
    agentName = "course-video",
    handoffReady = true,
    status = "ready",
    disabled = false,
    initialInput = "",
    onRetry,
    onSubmit = vi.fn(),
    showLocaleSwitch = false,
  }: {
    agentName?: AgentNewChatGuideId;
    handoffReady?: boolean;
    status?: "loading" | "error" | "ready";
    disabled?: boolean;
    initialInput?: string;
    onRetry?: () => void;
    onSubmit?: () => void;
    showLocaleSwitch?: boolean;
  } = {}) {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <PromptInputProvider initialInput={initialInput}>
            {showLocaleSwitch && <LocaleSwitch />}
            <AgentNewChatGuide
              agentName={agentName}
              handoffReady={handoffReady}
              status={status}
              disabled={disabled}
              onRetry={onRetry}
            >
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  onSubmit();
                }}
              >
                <TestComposer />
              </form>
            </AgentNewChatGuide>
          </PromptInputProvider>
        </I18nProvider>,
      );
    });
  }

  function caseButtons(): HTMLButtonElement[] {
    return Array.from(
      container.querySelectorAll<HTMLButtonElement>(
        "[data-testid='agent-guide-cases'] button",
      ),
    );
  }

  function composer(): HTMLTextAreaElement {
    const element = container.querySelector<HTMLTextAreaElement>(
      "textarea[name='message']",
    );
    if (!element) throw new Error("composer not found");
    return element;
  }

  function button(text: string): HTMLButtonElement {
    const element = Array.from(
      document.querySelectorAll<HTMLButtonElement>("button"),
    ).find((item) => item.textContent === text);
    if (!element) throw new Error(`button not found: ${text}`);
    return element;
  }

  async function click(element: HTMLElement) {
    await act(async () => {
      element.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it("renders the task hero, four cases, composer and tips in document flow", async () => {
    await renderGuide();

    expect(container.querySelector("h1")?.textContent).toBe(
      "把知识讲成一堂视频课",
    );
    expect(container.textContent).toContain("课程视频");
    expect(caseButtons()).toHaveLength(4);
    expect(caseButtons()[0]?.getAttribute("aria-label")).toBe("讲清一个知识点");
    expect(container.querySelector("svg.lucide-clapperboard")).not.toBeNull();
    expect(container.querySelector("svg.lucide-bot")).toBeNull();

    const guide = container.querySelector<HTMLElement>(
      "[data-testid='agent-new-chat-guide']",
    );
    expect(guide?.className).not.toContain("absolute");
    expect(guide?.className).not.toContain("translate-y");

    const cases = container.querySelector("[data-testid='agent-guide-cases']");
    const input = container.querySelector("textarea");
    const tips = container.querySelector("[data-testid='agent-guide-tips']");
    expect(cases?.compareDocumentPosition(input!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(input?.compareDocumentPosition(tips!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(tips?.textContent).toContain("告诉我主题、目标学员和期望时长。");
  });

  it("disables cases until handoff is ready and when the page is disabled", async () => {
    await renderGuide({ handoffReady: false, initialInput: "保留草稿" });
    expect(caseButtons().every((item) => item.disabled)).toBe(true);
    await click(caseButtons()[0]!);
    expect(composer().value).toBe("保留草稿");

    await renderGuide({ disabled: true });
    expect(caseButtons().every((item) => item.disabled)).toBe(true);
  });

  it("shows an honest loading state without rendering cases", async () => {
    await renderGuide({ status: "loading" });

    expect(container.textContent).toContain("正在加载创作助手");
    expect(caseButtons()).toHaveLength(0);
  });

  it("shows a recoverable error and calls retry without fake cases", async () => {
    const onRetry = vi.fn();
    await renderGuide({ status: "error", onRetry });

    expect(container.textContent).toContain("创作助手信息加载失败");
    expect(caseButtons()).toHaveLength(0);
    await click(button("重试"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("prefills and focuses an empty draft without submitting", async () => {
    const onSubmit = vi.fn();
    await renderGuide({ onSubmit });
    const prompt = zhCN.agents.newChatGuides["course-video"].cases[0].prompt;

    await click(caseButtons()[0]!);

    expect(composer().value).toBe(prompt);
    expect(document.activeElement).toBe(composer());
    expect(composer().selectionStart).toBe(prompt.length);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("cancels a case selection without changing a non-empty draft", async () => {
    const onSubmit = vi.fn();
    await renderGuide({ initialInput: "我的草稿", onSubmit });

    await click(caseButtons()[0]!);
    expect(document.body.textContent).toContain("输入框已有内容");
    expect(composer().value).toBe("我的草稿");
    await click(button("取消"));

    expect(composer().value).toBe("我的草稿");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("replaces a draft only after confirmation and never submits", async () => {
    const onSubmit = vi.fn();
    await renderGuide({ initialInput: "我的草稿", onSubmit });
    const prompt = zhCN.agents.newChatGuides["course-video"].cases[1].prompt;

    await click(caseButtons()[1]!);
    await click(button("替换"));

    expect(composer().value).toBe(prompt);
    expect(document.activeElement).toBe(composer());
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("appends a complete case prompt without discarding the draft", async () => {
    const onSubmit = vi.fn();
    await renderGuide({ initialInput: "我的草稿", onSubmit });
    const prompt = zhCN.agents.newChatGuides["course-video"].cases[2].prompt;

    await click(caseButtons()[2]!);
    await click(button("追加"));

    expect(composer().value).toBe(`我的草稿\n${prompt}`);
    expect(document.activeElement).toBe(composer());
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does not rewrite a filled draft when the locale changes", async () => {
    await renderGuide({ showLocaleSwitch: true });
    const zhPrompt = zhCN.agents.newChatGuides["course-video"].cases[0].prompt;
    const enPrompt = enUS.agents.newChatGuides["course-video"].cases[0].prompt;

    await click(caseButtons()[0]!);
    expect(composer().value).toBe(zhPrompt);
    await click(
      container.querySelector<HTMLElement>("[data-testid='locale-switch']")!,
    );
    expect(composer().value).toBe(zhPrompt);

    await click(caseButtons()[0]!);
    await click(button("Replace"));
    expect(composer().value).toBe(enPrompt);
  });
});
