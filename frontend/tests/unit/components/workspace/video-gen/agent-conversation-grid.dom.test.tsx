import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentConversationGrid } from "@/components/workspace/video-gen/agent-conversation-grid";
import type { AgentThread } from "@/core/threads/types";

const h = vi.hoisted(() => ({
  threads: [] as AgentThread[],
  isLoading: false,
  isError: false,
  refetch: vi.fn(),
  push: vi.fn(),
  params: undefined as Record<string, unknown> | undefined,
}));

vi.mock("@/core/threads/hooks", () => ({
  useThreads: (params: Record<string, unknown>) => {
    h.params = params;
    return {
      data: h.threads,
      isLoading: h.isLoading,
      isError: h.isError,
      refetch: h.refetch,
    };
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: h.push }),
}));

vi.mock("@/core/i18n/hooks", async () => {
  const { translations } = await import("@/core/i18n/translations");
  return { useI18n: () => ({ t: translations["zh-CN"] }) };
});

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function makeThread(
  id: string,
  options: {
    agentName?: string;
    projectType?: string;
    title?: string;
    createdAt?: string | null;
    updatedAt?: string | null;
    messages?: Array<{ type: string; content: string }>;
  } = {},
): AgentThread {
  const metadata = options.projectType
    ? { project_type: options.projectType }
    : { agent_name: options.agentName ?? "course-video" };
  return {
    thread_id: id,
    created_at:
      options.createdAt === undefined
        ? "2026-01-01T00:00:00Z"
        : options.createdAt,
    updated_at:
      options.updatedAt === undefined
        ? "2026-01-02T00:00:00Z"
        : options.updatedAt,
    metadata,
    status: "idle",
    values: {
      title: options.title ?? `对话 ${id}`,
      messages: options.messages ?? [],
      artifacts: [],
    },
    interrupts: {},
  } as unknown as AgentThread;
}

function button(text: string): HTMLButtonElement {
  const result = Array.from(document.querySelectorAll("button")).find(
    (item) => item.textContent === text,
  );
  if (!result) throw new Error(`button not found: ${text}`);
  return result;
}

function cardIds(): string[] {
  return Array.from(
    document.querySelectorAll("[data-testid='agent-conversation-card']"),
  ).map((card) => card.getAttribute("data-thread-id") ?? "");
}

function setSearch(value: string) {
  const input = document.querySelector<HTMLInputElement>(
    "input[type='search']",
  );
  if (!input) throw new Error("search input not found");
  Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("AgentConversationGrid", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.threads = [];
    h.isLoading = false;
    h.isError = false;
    h.params = undefined;
    h.refetch.mockReset();
    h.push.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.body.innerHTML = "";
  });

  async function render(
    agentName:
      | "course-video"
      | "creative-studio"
      | "ppt-master"
      | "podcast-studio" = "course-video",
    label = "文字动效",
  ) {
    await act(async () => {
      root.render(
        <AgentConversationGrid agentName={agentName} label={label} />,
      );
    });
  }

  it("requests complete history, filters exactly, dedupes and sorts stably", async () => {
    h.threads = [
      makeThread("b", { updatedAt: "2026-01-03T00:00:00Z" }),
      makeThread("a", { updatedAt: "2026-01-03T00:00:00Z" }),
      makeThread("created-fallback", {
        createdAt: "2026-02-01T00:00:00Z",
        updatedAt: null,
      }),
      makeThread("b", { title: "重复副本", updatedAt: "2026-01-03T00:00:00Z" }),
      makeThread("lead", { agentName: "lead_agent" }),
      makeThread("writer", { agentName: "co-writer" }),
      makeThread("unknown", { agentName: "unknown-agent" }),
      makeThread("ppt", { agentName: "ppt-master" }),
    ];
    await render();

    expect(h.params).toBeDefined();
    expect(h.params).not.toHaveProperty("limit");
    expect(h.params).toMatchObject({ sortBy: "updated_at", sortOrder: "desc" });
    expect(cardIds()).toEqual(["created-fallback", "a", "b"]);
    expect(container.textContent).not.toContain("重复副本");
    expect(container.textContent).not.toContain("对话 lead");
  });

  it("searches trimmed case-insensitive titles and paginates six at a time", async () => {
    h.threads = Array.from({ length: 13 }, (_, index) =>
      makeThread(`lesson-${index}`, {
        title: `Lesson ${String(index).padStart(2, "0")}`,
        updatedAt: `2026-02-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
      }),
    );
    await render();
    expect(cardIds()).toHaveLength(6);
    await act(async () => button("加载更多").click());
    expect(cardIds()).toHaveLength(12);
    await act(async () => button("加载更多").click());
    expect(cardIds()).toHaveLength(13);

    await act(async () => setSearch("  lEsSoN 07  "));
    expect(cardIds()).toEqual(["lesson-7"]);
    expect(container.querySelectorAll("video, img, a[download]")).toHaveLength(
      0,
    );
    expect(container.textContent).not.toContain("ready");
    expect(container.textContent).not.toContain("下载");

    await act(async () => setSearch("not present"));
    expect(cardIds()).toHaveLength(0);
    expect(container.textContent).toContain("没有匹配的对话");
    expect(container.textContent).not.toContain("暂无文字动效对话");
  });

  it("resets search and visible count when the agent changes", async () => {
    h.threads = Array.from({ length: 8 }, (_, index) =>
      makeThread(`course-${index}`),
    );
    await render();
    await act(async () => button("加载更多").click());
    await act(async () => setSearch("course"));
    expect(cardIds()).toHaveLength(8);

    h.threads = Array.from({ length: 8 }, (_, index) =>
      makeThread(`ppt-${index}`, { agentName: "ppt-master" }),
    );
    await render("ppt-master", "PPT课件");
    expect(
      document.querySelector<HTMLInputElement>("input[type='search']")?.value,
    ).toBe("");
    expect(cardIds()).toHaveLength(6);
    expect(cardIds().every((id) => id.startsWith("ppt-"))).toBe(true);
  });

  it.each([
    ["course-video", "文字动效"],
    ["creative-studio", "课程海报"],
    ["ppt-master", "PPT课件"],
    ["podcast-studio", "语音播客"],
  ] as const)(
    "renders the %s motif in an aligned card",
    async (agentName, label) => {
      h.threads = [makeThread(`motif-${agentName}`, { agentName })];
      await render(agentName, label);
      const card = container.querySelector<HTMLElement>(
        "[data-testid='agent-conversation-card']",
      )!;
      const motif = card.querySelector<HTMLElement>("[data-motif]");
      expect(motif?.getAttribute("data-motif")).toBe(agentName);
      expect(motif?.getAttribute("aria-hidden")).toBe("true");
      expect(motif?.className).toContain("aspect-video");
      expect(motif?.className).toContain("shrink-0");
      for (const className of ["h-full", "flex", "flex-col"]) {
        expect(card.className).toContain(className);
      }
      const body = card.lastElementChild;
      for (const className of ["flex", "flex-1", "flex-col"]) {
        expect(body?.className).toContain(className);
      }
      expect(button("继续对话").parentElement?.className).toContain("mt-auto");
    },
  );

  it("counts only real human messages as displayed turns", async () => {
    h.threads = [
      makeThread("two-turns", {
        messages: [
          { type: "human", content: "第一问" },
          { type: "ai", content: "回答" },
          { type: "human", content: "第二问" },
        ],
      }),
    ];
    await render();
    expect(
      container.querySelector("[data-thread-id='two-turns']")?.textContent,
    ).toContain("2 轮");
  });

  it("omits turns when values or messages are missing empty or have no human message", async () => {
    const missingValues = {
      ...makeThread("missing-values"),
      values: undefined,
    } as unknown as AgentThread;
    const missingMessages = {
      ...makeThread("missing-messages"),
      values: { title: "缺消息", artifacts: [] },
    } as unknown as AgentThread;
    const empty = makeThread("empty-messages");
    const noHuman = makeThread("no-human", {
      messages: [{ type: "ai", content: "只有助手消息" }],
    });

    for (const thread of [missingValues, missingMessages, empty, noHuman]) {
      h.threads = [thread];
      await render();
      expect(container.textContent).not.toContain("轮");
    }
  });

  it("keeps loading, error with retry, empty and no-result states separate", async () => {
    h.isLoading = true;
    await render();
    expect(container.textContent).toContain("正在加载对话");
    expect(container.textContent).not.toContain("暂无文字动效对话");

    h.isLoading = false;
    h.isError = true;
    await render();
    expect(container.textContent).toContain("对话加载失败，请稍后重试");
    expect(container.textContent).not.toContain("暂无文字动效对话");
    await act(async () => button("重试").click());
    expect(h.refetch).toHaveBeenCalledTimes(1);

    h.isError = false;
    await render();
    expect(container.textContent).toContain("暂无文字动效对话");
    expect(container.textContent).not.toContain("没有匹配的对话");
  });

  it("opens legacy aliases through canonical agent URLs without delete controls", async () => {
    h.threads = [
      makeThread("legacy-ppt", {
        projectType: "course-ppt",
        title: "旧版课件",
      }),
    ];
    await render("ppt-master", "PPT课件");
    expect(container.textContent).toContain("旧版课件");
    expect(container.textContent).toContain("PPT课件");
    expect(container.textContent).toContain("最近更新");
    expect(container.textContent).not.toContain("删除");
    await act(async () => button("继续对话").click());
    expect(h.push).toHaveBeenCalledExactlyOnceWith(
      "/workspace/agents/ppt-master/chats/legacy-ppt",
    );
  });
});
