/**
 * 模式阶梯（2026-11-21）：sendMessage 提交给 run 的 context 矩阵。
 *
 * PR2（2026-11-24 前端可靠性批）起 sendMessage 以 SDK onCreated 为受理点，所以这里
 * 改用真实 useStream + 可控 fake client：`runs.stream` 立即回调 onRunCreated 并以
 * 空流结束，`send()` 才会 resolve（原先只 resolve、不触发 onCreated 的替身会等到超时）。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LocalSettings } from "@/core/settings";
import { useThreadStream } from "@/core/threads/hooks";

/** 立即结束的空流（无任何事件）。 */
function emptyStream(): AsyncIterable<never> {
  return (async function* () {
    return;
  })();
}

const h = vi.hoisted(() => {
  const submitted: Array<{
    threadId: string;
    options: Record<string, unknown>;
  }> = [];
  const client = {
    threads: {
      getHistory: async () => [],
      getState: async () => ({ checkpoint: null, values: {} }),
      update: async () => ({}),
      updateState: async () => ({}),
    },
    runs: {
      stream: async (
        threadId: string,
        _assistantId: string,
        options: Record<string, unknown> & {
          onRunCreated?: (meta: { run_id: string; thread_id?: string }) => void;
        },
      ) => {
        submitted.push({ threadId, options });
        options.onRunCreated?.({ run_id: "run-1", thread_id: threadId });
        return emptyStream();
      },
      cancel: async () => ({}),
      joinStream: async () => emptyStream(),
      list: async () => [],
    },
  };
  return { submitted, client, models: [] as Array<Record<string, unknown>> };
});

vi.mock("@/core/api", () => ({ getAPIClient: () => h.client }));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
  getLangGraphBaseURL: () => "",
}));
vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: { uploads: { uploadingFiles: "Uploading" } } }),
}));
vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({ models: h.models, modeDefaults: {} }),
}));
vi.mock("@/core/tasks/context", () => ({
  useUpdateSubtask: () => () => undefined,
}));
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;
let send: (() => Promise<void>) | null = null;

function Probe({ context }: { context: LocalSettings["context"] }) {
  const { sendMessage } = useThreadStream({ threadId: "thread-1", context });
  useEffect(() => {
    send = () => sendMessage("thread-1", { text: "hello", files: [] });
    return () => {
      send = null;
    };
  }, [sendMessage]);
  return null;
}

beforeEach(() => {
  h.submitted.length = 0;
  h.models = [
    { name: "qwen", supports_thinking: true },
    { name: "deepseek", supports_thinking: true },
    { name: "claude", supports_thinking: false },
    { name: "qwen4", supports_thinking: true },
  ];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  send = null;
});

afterEach(() => {
  if (root) {
    act(() => root!.unmount());
  }
  container?.remove();
  container = null;
  root = null;
  send = null;
});

async function submitWithContext(context: LocalSettings["context"]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root!.render(
      <QueryClientProvider client={queryClient}>
        <Probe context={context} />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await send!();
  });
  return h.submitted[0]?.options.context as Record<string, unknown>;
}

describe("thread submit mode matrix", () => {
  it("flash disables thinking, planning, and subagents and ignores stale effort", async () => {
    const submitted = await submitWithContext({
      mode: "flash",
      model_name: "qwen",
      reasoning_effort: "low",
    });

    expect(submitted).toMatchObject({
      mode: "flash",
      thinking_enabled: false,
      reasoning_effort: "minimal",
      is_plan_mode: false,
      subagent_enabled: false,
    });
  });

  it("standard with DeepSeek enables thinking and planning with high effort", async () => {
    const submitted = await submitWithContext({
      mode: "standard",
      model_name: "deepseek",
      reasoning_effort: "low",
    } as LocalSettings["context"]);

    expect(submitted).toMatchObject({
      mode: "standard",
      thinking_enabled: true,
      reasoning_effort: "high",
      is_plan_mode: true,
      subagent_enabled: false,
    });
  });

  it("standard with Claude disables only thinking", async () => {
    const submitted = await submitWithContext({
      mode: "standard",
      model_name: "claude",
    } as LocalSettings["context"]);

    expect(submitted).toMatchObject({
      mode: "standard",
      thinking_enabled: false,
      reasoning_effort: "high",
      is_plan_mode: true,
      subagent_enabled: false,
    });
  });

  it("ultra enables the full execution matrix", async () => {
    const submitted = await submitWithContext({
      mode: "ultra",
      model_name: "qwen4",
      reasoning_effort: "low",
    });

    expect(submitted).toMatchObject({
      mode: "ultra",
      thinking_enabled: true,
      reasoning_effort: "high",
      is_plan_mode: true,
      subagent_enabled: true,
    });
  });

  it.each(["pro", "thinking"])(
    "legacy %s mode cannot override the standard matrix",
    async (mode) => {
      const submitted = await submitWithContext({
        mode: mode as never,
        model_name: "deepseek",
        reasoning_effort: "low",
      });

      expect(submitted).toMatchObject({
        mode: "standard",
        thinking_enabled: true,
        reasoning_effort: "high",
        is_plan_mode: true,
        subagent_enabled: false,
      });
    },
  );
});
