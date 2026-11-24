import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PromptInputProvider,
  usePromptInputAttachments,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";
import { AgentNewChatGuide } from "@/components/workspace/agent-new-chat-guide";
import type { AgentNewChatGuideId } from "@/core/agents/new-chat";
import { showcaseFor } from "@/core/agents/showcase";
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

function TestAttachments() {
  const { files, add } = usePromptInputAttachments();
  return (
    <>
      <button
        type="button"
        data-testid="add-attachment"
        onClick={() =>
          add([new File(["my notes"], "notes.txt", { type: "text/plain" })])
        }
      >
        add attachment
      </button>
      <span data-testid="attachment-count">{files.length}</span>
    </>
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
    handoffKey = "test-handoff",
    status = "ready",
    disabled = false,
    initialInput = "",
    onRetry,
    onSubmit = vi.fn(),
    showLocaleSwitch = false,
    showAttachments = false,
  }: {
    agentName?: AgentNewChatGuideId;
    handoffReady?: boolean;
    handoffKey?: string;
    status?: "loading" | "error" | "ready";
    disabled?: boolean;
    initialInput?: string;
    onRetry?: () => void;
    onSubmit?: () => void;
    showLocaleSwitch?: boolean;
    showAttachments?: boolean;
  } = {}) {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <PromptInputProvider initialInput={initialInput}>
            {showLocaleSwitch && <LocaleSwitch />}
            {showAttachments && <TestAttachments />}
            <AgentNewChatGuide
              agentName={agentName}
              handoffReady={handoffReady}
              handoffKey={handoffKey}
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
        "[data-testid='agent-guide-cases'] button[data-showcase-prompt], [data-testid='agent-guide-cases']:not([data-showcase]) button",
      ),
    );
  }

  function showcasePrompt(
    index: number,
    agentName: AgentNewChatGuideId = "course-video",
  ): string {
    const prompt = showcaseFor(agentName)?.cases[index]?.prompt;
    if (!prompt) throw new Error(`showcase prompt ${index} not found`);
    return prompt;
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

  it("showcase renders the new hero, three videos and composer in document flow", async () => {
    await renderGuide();
    const showcase = showcaseFor("course-video")!;

    expect(container.querySelector("h1")?.textContent).toBe(
      showcase.heroLine.zh.join(""),
    );
    expect(container.textContent).not.toContain("课程视频");
    expect(container.textContent).not.toContain("把知识与素材做成视频课");
    expect(caseButtons()).toHaveLength(3);
    expect(
      container.querySelector(
        "[data-testid='showcase-open-video-hallucination']",
      ),
    ).not.toBeNull();
    expect(container.querySelector("svg.lucide-clapperboard")).toBeNull();

    const guide = container.querySelector<HTMLElement>(
      "[data-testid='agent-new-chat-guide']",
    );
    expect(guide?.className).not.toContain("absolute");
    expect(guide?.className).not.toContain("translate-y");

    const cases = container.querySelector("[data-testid='agent-guide-cases']");
    const input = container.querySelector("textarea");
    expect(input?.compareDocumentPosition(cases!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(
      container.querySelector("[data-testid='agent-guide-tips']"),
    ).toBeNull();
  });

  it("legacy keeps the Co-writer header, four cases and composer order", async () => {
    await renderGuide({ agentName: "co-writer" });
    const guide = zhCN.agents.newChatGuides["co-writer"];

    expect(container.querySelector("h1")?.textContent).toBe(guide.title);
    expect(container.textContent).toContain(guide.description);
    expect(caseButtons()).toHaveLength(4);
    expect(container.querySelector("svg.lucide-pen-line")).not.toBeNull();
    const cases = container.querySelector("[data-testid='agent-guide-cases']");
    const input = container.querySelector("textarea");
    expect(cases?.compareDocumentPosition(input!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(
      container.querySelector("[data-testid='agent-guide-tips']"),
    ).toBeNull();
  });

  it("closes showcase media and resets cases when the agent changes in place", async () => {
    await renderGuide();
    await click(
      container.querySelector<HTMLElement>(
        "[data-testid='showcase-open-video-hallucination']",
      )!,
    );
    expect(document.querySelector("[role='dialog']")).not.toBeNull();

    await renderGuide({ agentName: "ppt-master" });

    expect(document.querySelector("[role='dialog']")).toBeNull();
    expect(caseButtons()).toHaveLength(3);
    expect(
      container.querySelector("[data-testid='showcase-open-ppt-quick']"),
    ).not.toBeNull();
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

  // The first fill-in placeholder of the inserted segment, never a section
  // header such as 【我的内容】.
  const placeholder = /【(?:请[^【】]*|可选填写[^【】]*)】/;

  function expectSelection(expected: string) {
    const input = composer();
    expect(input.value.slice(input.selectionStart, input.selectionEnd)).toBe(
      expected,
    );
    expect(input.selectionEnd - input.selectionStart).toBe(expected.length);
  }

  // First fill-in placeholder of the filled prompt, or the caret at the end
  // when the template has none (a fixed-script video takes an upload instead).
  function expectPlaceholderOrCaret(prompt: string) {
    const match = placeholder.exec(prompt);
    if (match) {
      expectSelection(match[0]);
      return;
    }
    expect(composer().selectionStart).toBe(prompt.length);
    expect(composer().selectionEnd).toBe(prompt.length);
  }

  it("prefills and focuses an empty draft without submitting", async () => {
    const onSubmit = vi.fn();
    await renderGuide({ agentName: "creative-studio", onSubmit });
    const prompt = showcasePrompt(0, "creative-studio");

    await click(caseButtons()[0]!);

    expect(composer().value).toBe(prompt);
    expect(document.activeElement).toBe(composer());
    // The section header 【我的内容】 comes first; the first fill-in wins.
    expectSelection("【请填写】");
    expect(composer().selectionStart).toBe(prompt.indexOf("课程主题：") + 5);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("selects the first fill-in placeholder inside the inserted segment only", async () => {
    await renderGuide({
      agentName: "creative-studio",
      initialInput: "草稿里的【请填写】不是目标",
    });
    const prompt = showcasePrompt(1, "creative-studio");

    await click(caseButtons()[1]!);
    await click(button("追加"));

    expect(composer().value).toBe(`草稿里的【请填写】不是目标\n${prompt}`);
    expectSelection("【请填写】");
    expect(composer().selectionStart).toBeGreaterThan(
      "草稿里的【请填写】不是目标\n".length,
    );
  });

  it("leaves the caret at the end when the filled prompt has no placeholder", async () => {
    await renderGuide({ agentName: "co-writer" });
    const prompt = zhCN.agents.newChatGuides["co-writer"].cases[0].prompt;
    expect(prompt).not.toMatch(/【/);

    await click(caseButtons()[0]!);

    expect(composer().value).toBe(prompt);
    expect(document.activeElement).toBe(composer());
    expect(composer().selectionStart).toBe(prompt.length);
  });

  // One row per agent; each slice appends its dialog as it gains the CTA.
  const dialogCases = [
    {
      agentName: "creative-studio",
      open: "showcase-open-visual-card-rag",
      promptId: "visual-card-rag",
    },
    {
      agentName: "course-video",
      open: "showcase-open-video-hallucination",
      promptId: "video-hallucination",
    },
    {
      agentName: "ppt-master",
      open: "showcase-open-ppt-standard",
      promptId: "ppt-standard",
    },
  ] as const;

  it.each(dialogCases)(
    "fills from the $agentName dialog CTA without sending or touching attachments",
    async ({ agentName, open, promptId }) => {
      const onSubmit = vi.fn();
      await renderGuide({ agentName, onSubmit, showAttachments: true });
      await click(button("add attachment"));
      const item = showcaseFor(agentName)!.cases.find(
        (entry) => entry.id === promptId,
      )!;

      await click(
        container.querySelector<HTMLElement>(`[data-testid='${open}']`)!,
      );
      await click(
        document.querySelector<HTMLElement>(
          "[data-testid='showcase-use-style']",
        )!,
      );

      // Preview closed, template filled, attachments untouched, nothing sent.
      expect(document.querySelector("[role='dialog']")).toBeNull();
      expect(composer().value).toBe(item.prompt);
      expect(
        container.querySelector("[data-testid='attachment-count']")
          ?.textContent,
      ).toBe("1");
      expect(document.activeElement).toBe(composer());
      expectPlaceholderOrCaret(item.prompt);
      expect(onSubmit).not.toHaveBeenCalled();
    },
  );

  it("routes the dialog CTA through the draft confirmation when the draft is not empty", async () => {
    await renderGuide({
      agentName: "creative-studio",
      initialInput: "我的草稿",
      showAttachments: true,
    });
    await click(button("add attachment"));
    const item = showcaseFor("creative-studio")!.cases.find(
      (entry) => entry.id === "visual-card-prompt",
    )!;

    await click(
      container.querySelector<HTMLElement>(
        "[data-testid='showcase-open-visual-card-prompt']",
      )!,
    );
    await click(
      document.querySelector<HTMLElement>(
        "[data-testid='showcase-use-style']",
      )!,
    );

    // Only the confirmation dialog is open and it holds the focus.
    const dialogs = document.querySelectorAll("[role='dialog']");
    expect(dialogs).toHaveLength(1);
    expect(document.body.textContent).toContain("输入框已有内容");
    expect(dialogs[0]!.contains(document.activeElement)).toBe(true);
    expect(composer().value).toBe("我的草稿");
    await click(button("替换"));

    expect(composer().value).toBe(item.prompt);
    expect(
      container.querySelector("[data-testid='attachment-count']")?.textContent,
    ).toBe("1");
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
    const prompt = showcasePrompt(1);

    await click(caseButtons()[1]!);
    await click(button("替换"));

    expect(composer().value).toBe(prompt);
    expect(document.activeElement).toBe(composer());
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("appends a complete case prompt without discarding the draft", async () => {
    const onSubmit = vi.fn();
    await renderGuide({ initialInput: "我的草稿", onSubmit });
    const prompt = showcasePrompt(2);

    await click(caseButtons()[2]!);
    await click(button("追加"));

    expect(composer().value).toBe(`我的草稿\n${prompt}`);
    expect(document.activeElement).toBe(composer());
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("invalidates a pending case when the handoff key changes", async () => {
    await renderGuide({ initialInput: "handoff A" });
    await click(caseButtons()[0]!);
    expect(document.body.textContent).toContain("输入框已有内容");

    await renderGuide({
      initialInput: "handoff A",
      handoffKey: "changed-handoff",
      handoffReady: false,
    });
    expect(document.querySelector("[role='dialog']")).toBeNull();

    await renderGuide({
      initialInput: "handoff A",
      handoffKey: "changed-handoff",
      handoffReady: true,
    });
    expect(document.querySelector("[role='dialog']")).toBeNull();
    expect(composer().value).toBe("handoff A");
  });

  it("showcase keeps prompts Chinese and does not rewrite a filled draft on locale change", async () => {
    await renderGuide({ showLocaleSwitch: true });
    const zhPrompt = showcasePrompt(0);

    await click(caseButtons()[0]!);
    expect(composer().value).toBe(zhPrompt);
    await click(
      container.querySelector<HTMLElement>("[data-testid='locale-switch']")!,
    );
    expect(composer().value).toBe(zhPrompt);

    await click(caseButtons()[0]!);
    await click(button("Replace"));
    expect(composer().value).toBe(zhPrompt);
  });

  it("legacy Co-writer keeps localized English prompts", async () => {
    await renderGuide({ agentName: "co-writer", showLocaleSwitch: true });
    const zhPrompt = zhCN.agents.newChatGuides["co-writer"].cases[0].prompt;
    const enPrompt = enUS.agents.newChatGuides["co-writer"].cases[0].prompt;

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
