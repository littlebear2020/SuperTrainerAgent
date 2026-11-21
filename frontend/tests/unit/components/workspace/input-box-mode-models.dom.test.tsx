import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
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
vi.mock("@/core/models/hooks", () => ({ useModels: vi.fn() }));
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
import { enUS } from "@/core/i18n/locales";
import { useModels } from "@/core/models/hooks";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const roster = [
  ["qwen3.8-flash", ["flash"]],
  ["deepseek-v4.1-flash", ["flash", "standard"]],
  ["qwen4-max", ["ultra"]],
  ["kimi-k3.1-pro", ["ultra"]],
  ["claude-sonnet-5-5", ["standard", "ultra"]],
  ["internal-only", []],
].map(([name, modes]) => ({
  id: name as string,
  name: name as string,
  model: name as string,
  display_name: name as string,
  modes: modes as string[],
  supports_thinking: name !== "claude-sonnet-5-5",
}));

type InputBoxProps = Parameters<typeof InputBox>[0];
type ContextProp = InputBoxProps["context"];
type OnContextChangeProp = InputBoxProps["onContextChange"];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (useModels as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    models: roster,
    modeDefaults: {
      flash: "qwen3.8-flash",
      standard: "claude-sonnet-5-5",
      ultra: "qwen4-max",
    },
    tokenUsageEnabled: false,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

async function renderInputBox(
  context: ContextProp,
  onContextChange = vi.fn(),
  onSubmit = vi.fn(),
) {
  document.cookie = "locale=en-US; path=/";
  await act(async () => {
    root.render(
      <I18nProvider initialLocale="en-US">
        <ThreadContext.Provider value={{ thread: { messages: [] } as never }}>
          <PromptInputProvider initialInput="hello">
            <InputBox
              threadId="thread-1"
              context={context}
              onSubmit={onSubmit}
              onContextChange={
                onContextChange as unknown as OnContextChangeProp
              }
            />
          </PromptInputProvider>
        </ThreadContext.Provider>
      </I18nProvider>,
    );
  });
  return onContextChange;
}

function buttonWithText(text: string): HTMLButtonElement {
  const button = Array.from(
    container.querySelectorAll<HTMLButtonElement>("button"),
  ).find(
    (candidate) =>
      candidate.textContent?.includes(text) ||
      candidate.getAttribute("aria-label")?.includes(text),
  );
  if (!button) throw new Error(`Button not found: ${text}`);
  return button;
}

async function click(element: HTMLElement) {
  await act(async () => {
    element.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function modeMenuItems(): HTMLElement[] {
  return Array.from(
    document.body.querySelectorAll<HTMLElement>("[role='menuitem']"),
  );
}

async function selectMode(label: string, description: string) {
  await click(buttonWithText(label));
  const item = modeMenuItems().find((candidate) =>
    candidate.textContent?.includes(description),
  );
  expect(item).toBeTruthy();
  await click(item!);
}

describe("mode-aware model selection", () => {
  it("uses standard + Claude for a new unset context", async () => {
    const onContextChange = await renderInputBox({
      model_name: undefined,
      mode: undefined,
    });

    expect(onContextChange).toHaveBeenCalledWith(
      expect.objectContaining({
        model_name: "claude-sonnet-5-5",
        mode: "standard",
      }),
    );
  });

  it("switches from standard to the flash default without persisting effort", async () => {
    const onContextChange = await renderInputBox({
      model_name: "claude-sonnet-5-5",
      mode: "standard",
    } as ContextProp);
    onContextChange.mockClear();

    await selectMode(
      enUS.inputBox.standardMode,
      enUS.inputBox.flashModeDescription,
    );

    expect(onContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        model_name: "qwen3.8-flash",
        mode: "flash",
      }),
    );
    expect(onContextChange.mock.lastCall?.[0]).not.toHaveProperty(
      "reasoning_effort",
    );
  });

  it("switches to the Ultra default only when the current model is illegal", async () => {
    const onContextChange = await renderInputBox({
      model_name: "qwen3.8-flash",
      mode: "flash",
    });
    onContextChange.mockClear();

    await selectMode(
      enUS.inputBox.flashMode,
      enUS.inputBox.ultraModeDescription,
    );

    expect(onContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ model_name: "qwen4-max", mode: "ultra" }),
    );
  });

  it("keeps Claude when switching from standard to Ultra", async () => {
    const onContextChange = await renderInputBox({
      model_name: "claude-sonnet-5-5",
      mode: "standard",
    } as ContextProp);
    onContextChange.mockClear();

    await selectMode(
      enUS.inputBox.standardMode,
      enUS.inputBox.ultraModeDescription,
    );

    expect(onContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        model_name: "claude-sonnet-5-5",
        mode: "ultra",
      }),
    );
  });

  it("lists only standard models and does not report cross-mode filtering as delisting", async () => {
    await renderInputBox({
      model_name: "claude-sonnet-5-5",
      mode: "standard",
    } as ContextProp);

    await click(buttonWithText("claude-sonnet-5-5"));
    const options = Array.from(
      document.body.querySelectorAll<HTMLElement>("[cmdk-item]"),
    );

    expect(options.map((option) => option.textContent)).toEqual([
      expect.stringContaining("deepseek-v4.1-flash"),
      expect.stringContaining("claude-sonnet-5-5"),
    ]);
    expect(toast.info).not.toHaveBeenCalled();
  });

  it("renders three modes without reasoning-mode or effort controls", async () => {
    await renderInputBox({ model_name: "qwen3.8-flash", mode: "flash" });

    await click(buttonWithText(enUS.inputBox.flashMode));
    const items = modeMenuItems();

    expect(items).toHaveLength(3);
    expect(document.body.textContent).not.toContain("Reasoning Effort");
    expect(document.body.textContent).not.toContain("Reasoning before action");
  });
});

describe("submit guard", () => {
  it("corrects an illegal model to the mode default before submitting", async () => {
    const onSubmit = vi.fn();
    const onContextChange = await renderInputBox(
      {
        model_name: "qwen3.8-flash",
        mode: "standard",
      } as ContextProp,
      vi.fn(),
      onSubmit,
    );
    onContextChange.mockClear();

    await click(buttonWithText("Submit"));

    expect(onContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        model_name: "claude-sonnet-5-5",
        mode: "standard",
      }),
    );
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ text: "hello" }),
    );
  });
});
