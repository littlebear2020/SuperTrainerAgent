/**
 * P0-3 composer placeholder contract (2026-11-17 agent showcase visual
 * fidelity plan):
 *
 * - `InputBox` keeps the global `t.inputBox.placeholder` when no `placeholder`
 *   prop is supplied (existing threads, co-writer, every other surface).
 * - An explicit string (only the four showcase new-chat guides pass one) wins.
 * - The prop is a plain `string`, never a `LocalizedText` object.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  useModels: vi.fn(),
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
import { showcaseFor } from "@/core/agents/showcase";
import { I18nProvider } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales";
import { useModels } from "@/core/models/hooks";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const fakeThread = { messages: [] } as never;

let container: HTMLDivElement;
let root: Root;

function mockModels() {
  (useModels as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    models: [
      {
        name: "m1",
        display_name: "Model One",
        model: "m1",
        supports_thinking: false,
      },
    ],
    tokenUsageEnabled: false,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
}

async function renderInputBox(placeholder?: string) {
  await act(async () => root.unmount());
  root = createRoot(container);
  await act(async () => {
    root.render(
      <I18nProvider initialLocale="en-US">
        <ThreadContext.Provider value={{ thread: fakeThread }}>
          <PromptInputProvider initialInput="">
            <InputBox
              threadId="t1"
              context={{ model_name: "m1", mode: "flash" }}
              placeholder={placeholder}
            />
          </PromptInputProvider>
        </ThreadContext.Provider>
      </I18nProvider>,
    );
  });
}

function textarea() {
  const element = document.querySelector<HTMLTextAreaElement>(
    "textarea[name='message']",
  );
  if (!element) throw new Error("composer textarea not rendered");
  return element;
}

beforeEach(() => {
  document.cookie = "locale=en-US; path=/";
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  mockModels();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("InputBox placeholder", () => {
  it("defaults to the global composer sentence", async () => {
    await renderInputBox();
    expect(textarea().placeholder).toBe(enUS.inputBox.placeholder);
  });

  it("lets a showcase agent pass a string placeholder", async () => {
    const placeholder = showcaseFor("course-video")!.inputPlaceholder.en;
    await renderInputBox(placeholder);
    expect(textarea().placeholder).toBe(placeholder);
    expect(textarea().placeholder).not.toBe(enUS.inputBox.placeholder);
  });
});
