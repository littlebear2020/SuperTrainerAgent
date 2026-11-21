import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LocalSettings } from "@/core/settings";
import { useThreadStream } from "@/core/threads/hooks";

const h = vi.hoisted(() => ({
  submit: vi.fn(),
  models: [] as Array<Record<string, unknown>>,
}));

vi.mock("@langchain/langgraph-sdk/react", () => ({
  useStream: () => ({ messages: [], submit: h.submit }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: [], isLoading: false }),
  useQueryClient: () => ({
    setQueriesData: vi.fn(),
    invalidateQueries: vi.fn(),
  }),
  useMutation: vi.fn(),
}));

vi.mock("@/core/api", () => ({ getAPIClient: () => ({}) }));
vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: { uploads: { uploadingFiles: "Uploading" } } }),
}));
vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({ models: h.models, modeDefaults: {} }),
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
  h.submit.mockReset();
  h.submit.mockResolvedValue(undefined);
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
  await act(async () => {
    root!.render(<Probe context={context} />);
  });
  await act(async () => {
    await send!();
  });
  return h.submit.mock.calls[0]?.[1]?.context as Record<string, unknown>;
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
