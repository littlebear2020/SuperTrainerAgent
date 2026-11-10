import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentNewChatGuide } from "@/components/workspace/agent-new-chat-guide";
import type { AgentNewChatGuideId } from "@/core/agents/new-chat";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

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
    onRetry,
  }: {
    agentName?: AgentNewChatGuideId;
    handoffReady?: boolean;
    status?: "loading" | "error" | "ready";
    disabled?: boolean;
    onRetry?: () => void;
  } = {}) {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <AgentNewChatGuide
            agentName={agentName}
            handoffReady={handoffReady}
            status={status}
            disabled={disabled}
            onRetry={onRetry}
          >
            <textarea data-testid="composer" name="message" />
          </AgentNewChatGuide>
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
    const composer = container.querySelector("[data-testid='composer']");
    const tips = container.querySelector("[data-testid='agent-guide-tips']");
    expect(cases?.compareDocumentPosition(composer!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(composer?.compareDocumentPosition(tips!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(tips?.textContent).toContain("告诉我主题、目标学员和期望时长。");
  });

  it("disables cases until handoff is ready and when the page is disabled", async () => {
    await renderGuide({ handoffReady: false });
    expect(caseButtons().every((button) => button.disabled)).toBe(true);

    await renderGuide({ disabled: true });
    expect(caseButtons().every((button) => button.disabled)).toBe(true);
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
    const retry = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "重试",
    );
    expect(retry).toBeDefined();
    await act(async () => retry?.click());
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
