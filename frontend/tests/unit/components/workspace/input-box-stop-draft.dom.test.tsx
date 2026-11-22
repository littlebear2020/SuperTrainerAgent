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
  useModels: () => ({
    models: [
      {
        name: "m1",
        display_name: "Model One",
        model: "m1",
        modes: ["flash"],
        supports_thinking: false,
      },
    ],
    tokenUsageEnabled: false,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/core/skills/hooks", () => ({
  useSkills: () => ({ skills: [], isLoading: false }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

import {
  PromptInputProvider,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import { InputBox } from "@/components/workspace/input-box";
import { ThreadContext } from "@/components/workspace/messages/context";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const fakeThread = { messages: [] } as never;
const draft = "停止后仍要保留的草稿";
const storedDrafts = new Map<string, string>();
const localStorageStub = {
  getItem: (key: string) => storedDrafts.get(key) ?? null,
  setItem: (key: string, value: string) => storedDrafts.set(key, value),
  removeItem: (key: string) => storedDrafts.delete(key),
  clear: () => storedDrafts.clear(),
};
Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: localStorageStub,
});

let container: HTMLDivElement;
let root: Root;
let onStop: ReturnType<typeof vi.fn<() => void>>;
let onSubmit: ReturnType<typeof vi.fn<(message: PromptInputMessage) => void>>;

function textarea(): HTMLTextAreaElement {
  const element = container.querySelector<HTMLTextAreaElement>(
    "textarea[name='message']",
  );
  if (!element) throw new Error("composer textarea not rendered");
  return element;
}

async function renderComposer(status: "ready" | "streaming") {
  await act(async () => {
    root.render(
      <I18nProvider initialLocale="zh-CN">
        <ThreadContext.Provider value={{ thread: fakeThread }}>
          <PromptInputProvider initialInput={draft}>
            <InputBox
              threadId="t1"
              context={{ model_name: "m1", mode: "flash" }}
              status={status}
              onStop={onStop}
              onSubmit={onSubmit}
            />
          </PromptInputProvider>
        </ThreadContext.Provider>
      </I18nProvider>,
    );
  });

  const fileInput =
    container.querySelector<HTMLInputElement>("input[type='file']");
  if (!fileInput) throw new Error("file input not rendered");
  Object.defineProperty(fileInput, "files", {
    configurable: true,
    value: [new File(["draft"], "draft.txt", { type: "text/plain" })],
  });
  await act(async () => {
    fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(container.textContent).toContain("draft.txt");
}

async function clickStop() {
  const button = container.querySelector<HTMLButtonElement>(
    "button[type='submit'][aria-label='Submit']",
  );
  if (!button) throw new Error("submit button not rendered");
  await act(async () => {
    button.click();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function pressEnter() {
  await act(async () => {
    textarea().dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Enter",
        bubbles: true,
        cancelable: true,
      }),
    );
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function expectStreamingStopPreserved(trigger: () => Promise<void>) {
  await renderComposer("streaming");
  await trigger();
  expect(onStop).toHaveBeenCalledTimes(1);
  expect(onSubmit).not.toHaveBeenCalled();
  expect(textarea().value).toBe(draft);
  expect(container.textContent).toContain("draft.txt");

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 450));
  });
  expect(window.localStorage.getItem("draft:t1")).toBe(draft);
  expect(textarea().value).toBe(draft);
  expect(container.textContent).toContain("draft.txt");
}

beforeEach(() => {
  window.localStorage.clear();
  onStop = vi.fn();
  onSubmit = vi.fn();
  const urlApi = URL as unknown as {
    createObjectURL?: (blob: Blob) => string;
    revokeObjectURL?: (url: string) => void;
  };
  urlApi.createObjectURL ??= () => "blob:draft";
  urlApi.revokeObjectURL ??= () => undefined;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  window.localStorage.clear();
  vi.clearAllMocks();
});

describe("InputBox stop preserves an in-progress draft", () => {
  it("preserves text, attachment, and persisted draft after the stop button", async () => {
    await expectStreamingStopPreserved(clickStop);
  });

  it("preserves text, attachment, and persisted draft after Enter", async () => {
    await expectStreamingStopPreserved(pressEnter);
  });

  it("still submits and clears a normal non-streaming message", async () => {
    await renderComposer("ready");
    await clickStop();

    expect(onStop).not.toHaveBeenCalled();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ text: draft }),
    );
    expect(textarea().value).toBe("");
    expect(container.textContent).not.toContain("draft.txt");

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 450));
    });
    expect(window.localStorage.getItem("draft:t1")).toBeNull();
  });
});
