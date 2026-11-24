/**
 * PR2（2026-11-24 前端可靠性批）：InputBox 把 onSubmit 的 accepted Promise 原样交给
 * registry PromptInput（Provider 模式：Promise resolve 后才清空正文与附件）。
 *
 * - 受理前停止 / 创建前失败 / busy → 正文、附件与 draft:{tid} 全部保留
 * - 纯附件可发；空白正文且无附件不发送
 * - 模型自动校正分支只延迟、不提前 resolve
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

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
import type { LocalSettings } from "@/core/settings";
import { SendRejectedError } from "@/core/threads/submission";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const fakeThread = { messages: [] } as never;
const draft = "受理前不能丢的草稿";
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
let onSubmit: ReturnType<
  typeof vi.fn<(message: PromptInputMessage) => Promise<void> | void>
>;
let onContextChange: ReturnType<
  typeof vi.fn<(context: LocalSettings["context"]) => void>
>;

function textarea(): HTMLTextAreaElement {
  const element = container.querySelector<HTMLTextAreaElement>(
    "textarea[name='message']",
  );
  if (!element) throw new Error("composer textarea not rendered");
  return element;
}

async function renderComposer(
  options: {
    initialInput?: string;
    context?: { model_name: string; mode: "flash" };
    withAttachment?: boolean;
  } = {},
) {
  await act(async () => {
    root.render(
      <I18nProvider initialLocale="zh-CN">
        <ThreadContext.Provider value={{ thread: fakeThread }}>
          <PromptInputProvider initialInput={options.initialInput ?? draft}>
            <InputBox
              threadId="t1"
              context={options.context ?? { model_name: "m1", mode: "flash" }}
              status="ready"
              onSubmit={onSubmit}
              onContextChange={onContextChange}
            />
          </PromptInputProvider>
        </ThreadContext.Provider>
      </I18nProvider>,
    );
  });

  if (options.withAttachment ?? true) {
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
  }
}

async function clickSubmit() {
  const button = container.querySelector<HTMLButtonElement>(
    "button[type='submit'][aria-label='Submit']",
  );
  if (!button) throw new Error("submit button not rendered");
  await act(async () => {
    button.click();
  });
  await settle();
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
  });
  await settle();
}

async function settle(ms = 30) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

async function waitDebounce() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 450));
  });
}

function rejected(kind: "busy" | "stopped" | "failed") {
  return Promise.reject(new SendRejectedError(kind, `${kind} for test`));
}

beforeEach(() => {
  window.localStorage.clear();
  onSubmit = vi.fn();
  onContextChange = vi.fn();
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
  // 本文件里 onSubmit 的 rejection 由 registry PromptInput 观察；这里兜住用例自己
  // 故意不观察的 promise，避免污染后续用例的 unhandled 统计
  vi.clearAllMocks();
  await act(async () => root.unmount());
  container.remove();
  window.localStorage.clear();
});

describe("InputBox 受理前保留输入", () => {
  test.each(["stopped", "failed", "busy"] as const)(
    "%s：正文、附件与 draft:{tid} 全部保留",
    async (kind) => {
      onSubmit.mockImplementation(() => rejected(kind));
      await renderComposer();
      await waitDebounce();
      expect(window.localStorage.getItem("draft:t1")).toBe(draft);

      await clickSubmit();

      expect(onSubmit).toHaveBeenCalledTimes(1);
      expect(textarea().value).toBe(draft);
      expect(container.textContent).toContain("draft.txt");
      await waitDebounce();
      expect(window.localStorage.getItem("draft:t1")).toBe(draft);
    },
  );

  test("accepted 后才清空正文与附件", async () => {
    let accept!: () => void;
    onSubmit.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          accept = resolve;
        }),
    );
    await renderComposer();
    await clickSubmit();

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(textarea().value).toBe(draft);
    expect(container.textContent).toContain("draft.txt");

    await act(async () => {
      accept();
    });
    await settle();
    expect(textarea().value).toBe("");
    expect(container.textContent).not.toContain("draft.txt");
    await waitDebounce();
    expect(window.localStorage.getItem("draft:t1")).toBeNull();
  });

  test("纯附件会发送；空白正文且无附件不发送", async () => {
    await renderComposer({ initialInput: "", withAttachment: true });
    expect(textarea().value).toBe("");
    expect(container.textContent).toContain("draft.txt");
    await clickSubmit();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]![0]).toMatchObject({ text: "" });
    expect(onSubmit.mock.calls[0]![0].files).toHaveLength(1);

    onSubmit.mockClear();
    await renderComposer({ initialInput: "", withAttachment: false });
    await pressEnter();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  test("模型自动校正分支只延迟一拍，不提前 resolve", async () => {
    let accept!: () => void;
    onSubmit.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          accept = resolve;
        }),
    );
    // 持久化的 model_name 不在册：提交时先自动校正再发送
    await renderComposer({
      context: { model_name: "stale-model", mode: "flash" },
    });
    await clickSubmit();

    expect(onContextChange).toHaveBeenCalled();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(textarea().value).toBe(draft);
    expect(container.textContent).toContain("draft.txt");

    await act(async () => {
      accept();
    });
    await settle();
    expect(textarea().value).toBe("");
  });

  test("受理前等待期间用户继续输入的文字，在受理后保留（registry 清空后恢复）", async () => {
    let accept!: () => void;
    onSubmit.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          accept = resolve;
        }),
    );
    await renderComposer();
    await clickSubmit();
    expect(textarea().value).toBe(draft);

    // 等待受理期间继续输入（例如上传大附件的几秒钟）
    act(() => {
      const el = textarea();
      el.value = "等待期间继续写的新内容";
      (
        el as HTMLTextAreaElement & {
          _valueTracker?: { setValue(v: string): void };
        }
      )._valueTracker?.setValue("");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(textarea().value).toBe("等待期间继续写的新内容");

    await act(async () => {
      accept();
    });
    // registry 在 Promise resolve 后清空；InputBox 随后恢复等待期间的新输入
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(textarea().value).toBe("等待期间继续写的新内容");
  });

  test("受理前等待期间追加的文字只恢复增量，不把已发出的正文放回（OCR 第 2 轮）", async () => {
    let accept!: () => void;
    onSubmit.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          accept = resolve;
        }),
    );
    await renderComposer();
    await clickSubmit();
    // 等待期间在已提交正文之后追加
    act(() => {
      const el = textarea();
      el.value = `${draft} 继续补的半句`;
      (
        el as HTMLTextAreaElement & {
          _valueTracker?: { setValue(v: string): void };
        }
      )._valueTracker?.setValue("");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => {
      accept();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    // 已发出的 draft 不再出现，只有等待期间补的半句
    expect(textarea().value).toBe(" 继续补的半句");
  });
});
