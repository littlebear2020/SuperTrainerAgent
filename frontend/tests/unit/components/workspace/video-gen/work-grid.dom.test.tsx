import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkGrid } from "@/components/workspace/video-gen/work-grid";
import type { AgentThread } from "@/core/threads/types";
import { pathOfThread } from "@/core/threads/utils";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import { buildTaskWorks } from "@/core/works/model";
import type {
  AgentDeliveryState,
  WorkFile,
  WorkSummary,
} from "@/core/works/types";
import type { WorkCapability } from "@/core/workspace/video-assets-route";

const ui = vi.hoisted(() => ({
  locale: "zh-CN" as "zh-CN" | "en-US",
  recomposePending: false,
}));

const recompose = vi.hoisted(() => ({
  calls: [] as Array<{
    id: string;
    mutate: ReturnType<typeof vi.fn<() => void>>;
  }>,
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useRecomposeTask: (id: string) => {
    const mutate = vi.fn<() => void>();
    recompose.calls.push({ id, mutate });
    return { mutate, isPending: ui.recomposePending };
  },
}));

vi.mock("@/core/i18n/hooks", async () => {
  const { translations } = await import("@/core/i18n/translations");
  return { useI18n: () => ({ t: translations[ui.locale] }) };
});

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function makeTask(
  id: string,
  overrides: Partial<VideoTaskBrief> = {},
): VideoTaskBrief {
  return {
    id,
    user_id: "user-1",
    domain: "video_gen",
    task_type: "ai_video",
    status: "ready",
    progress: 100,
    title: `作品 ${id}`,
    error_message: null,
    retry_count: 0,
    dirty_frames: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    thumbnail_url: `/api/video/tasks/${id}/thumbnail`,
    artifact_missing: false,
    cost: null,
    currency: null,
    ...overrides,
  };
}

function makeTaskWork(
  id: string,
  overrides: Partial<VideoTaskBrief> = {},
): WorkSummary {
  const [work] = buildTaskWorks([makeTask(id, overrides)], "ai_video");
  if (!work) throw new Error("task work not built");
  return work;
}

function makeAgentFile(
  name: string,
  overrides: Partial<WorkFile> = {},
): WorkFile {
  return {
    id: `thread:thread-1:/mnt/user-data/outputs/${name}`,
    name,
    mediaKind: "document",
    size: 1234,
    modifiedAt: "2026-01-03T00:00:00Z",
    exists: true,
    previewUrl: null,
    downloadUrl: `/api/threads/thread-1/artifacts/${name}?download=true`,
    ...overrides,
  };
}

function makeAgentWork(
  state: AgentDeliveryState,
  files: WorkFile[] = [],
  overrides: { title?: string; projectType?: string } = {},
): WorkSummary {
  const thread = {
    thread_id: "thread-1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    metadata: overrides.projectType
      ? { project_type: overrides.projectType }
      : { agent_name: "ppt-master" },
    values: {
      title: overrides.title ?? "Agent 作品",
      messages: [],
      artifacts: [],
    },
  } as unknown as AgentThread;
  return {
    id: "thread:thread-1",
    capability: "ppt-master",
    title: overrides.title ?? "Agent 作品",
    source: { kind: "agent", thread },
    agentDeliveryState: state,
    sortAt: "2026-01-02T00:00:00Z",
    files,
    primaryFile: files.find((file) => file.exists) ?? null,
  };
}

function cards(): HTMLElement[] {
  return Array.from(
    document.querySelectorAll<HTMLElement>("[data-testid='work-card']"),
  );
}

function button(text: string, scope: ParentNode = document): HTMLButtonElement {
  const result = Array.from(scope.querySelectorAll("button")).find((item) =>
    item.textContent?.includes(text),
  );
  if (!result) throw new Error(`button not found: ${text}`);
  return result;
}

function dialogButton(text: string): HTMLButtonElement {
  return button(
    text,
    document.querySelector("[role='alertdialog']") ?? document,
  );
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

interface RenderOptions {
  renderKey?: string;
  capability?: WorkCapability;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void | Promise<void>;
  onContinue?: (work: WorkSummary) => void;
  onTaskNavigate?: (task: VideoTaskBrief, path?: "storyboard") => void;
  onDelete?: (id: string) => void;
}

describe("WorkGrid", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    ui.locale = "zh-CN";
    ui.recomposePending = false;
    recompose.calls.length = 0;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.body.innerHTML = "";
  });

  async function render(works: WorkSummary[], props: RenderOptions = {}) {
    await act(async () => {
      root.render(
        <WorkGrid
          key={props.renderKey ?? "default"}
          works={works}
          capability={props.capability ?? works[0]?.capability ?? "ai_video"}
          isLoading={props.isLoading}
          isError={props.isError}
          onRetry={props.onRetry ?? (() => undefined)}
          onContinue={props.onContinue ?? (() => undefined)}
          onTaskNavigate={props.onTaskNavigate ?? (() => undefined)}
          onDelete={props.onDelete ?? (() => undefined)}
        />,
      );
    });
  }

  it("renders six then six more and keeps more than the old 100-item cap reachable", async () => {
    const onRetry = vi.fn<() => void>();
    const works = Array.from({ length: 105 }, (_, index) =>
      makeTaskWork(`task-${index}`),
    );
    await render(works, { onRetry });

    expect(cards()).toHaveLength(6);
    await act(async () => button("加载更多").click());
    expect(cards()).toHaveLength(12);
    for (let index = 0; index < 16; index += 1) {
      await act(async () => button("加载更多").click());
    }
    expect(cards()).toHaveLength(105);
    expect(onRetry).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("轮对话");
  });

  it("searches titles and file names, reports record counts, and clears no-results", async () => {
    const agent = makeAgentWork(
      "delivered",
      [makeAgentFile("Quarterly Report.pptx")],
      { title: "Alpha Course" },
    );
    const other = makeTaskWork("other", { title: "Beta Video" });
    await render([agent, other], { capability: "ppt-master" });
    expect(document.body.textContent).toContain("2 个创作记录");

    await act(async () => setSearch("  quarterly report  "));
    expect(cards()).toHaveLength(1);
    expect(cards()[0]?.getAttribute("data-work-id")).toBe(agent.id);
    expect(document.body.textContent).toContain("1 个创作记录");

    await act(async () => setSearch("alpha course"));
    expect(cards()).toHaveLength(1);
    await act(async () => setSearch("nothing matches"));
    expect(cards()).toHaveLength(0);
    expect(document.body.textContent).toContain("没有匹配的作品");
    await act(async () => button("清除搜索").click());
    expect(cards()).toHaveLength(2);
  });

  it("resets search, pagination, and the open viewer when keyed capability changes", async () => {
    const works = Array.from({ length: 13 }, (_, index) =>
      makeTaskWork(`task-${index}`),
    );
    await render(works, { renderKey: "ai_video" });
    await act(async () => button("加载更多").click());
    expect(cards()).toHaveLength(12);
    await act(async () => button("查看", cards()[0]).click());
    expect(document.querySelector("[role='dialog']")).not.toBeNull();
    await act(async () => setSearch("task-12"));
    expect(cards()).toHaveLength(1);

    await render(works, {
      renderKey: "course-video",
      capability: "course-video",
    });

    expect(cards()).toHaveLength(6);
    expect(document.querySelector("input[type='search']")).toHaveProperty(
      "value",
      "",
    );
    expect(document.querySelector("[role='dialog']")).toBeNull();
    expect(
      document.querySelector("[data-testid='works-view'] [role='tablist']"),
    ).toBeNull();
  });

  it("keeps loading, error, empty, and no-results states distinct and preserves works on refresh error", async () => {
    await render([], { isLoading: true });
    expect(document.body.textContent).toContain("正在加载作品");
    expect(document.body.textContent).not.toContain("暂无");

    const onRetry = vi.fn<() => void>();
    await render([], { isError: true, onRetry });
    expect(document.body.textContent).toContain("作品加载失败");
    expect(document.body.textContent).not.toContain("暂无");
    await act(async () => button("重试").click());
    expect(onRetry).toHaveBeenCalledTimes(1);

    await render([], { capability: "creative-studio" });
    expect(document.body.textContent).toContain("暂无课程海报作品");

    const work = makeTaskWork("kept");
    await render([work], { isError: true, onRetry });
    expect(cards()).toHaveLength(1);
    expect(cards()[0]?.getAttribute("data-work-id")).toBe(work.id);
    expect(document.body.textContent).toContain("作品加载失败");
    expect(document.body.textContent).not.toContain("暂无AI视频作品");
  });

  it("uses honest fields and stable equal-height card structure without invented stats", async () => {
    const fixture = makeTaskWork("honest", {
      title: "真实课程封面",
      delivery: {
        file_name: "zero.mp4",
        file_size: 0,
        file_updated_at: "2026-01-02T00:00:00Z",
        media_kind: "video",
        exists: true,
      },
      total_duration: 99,
      frame_count: 88,
      shot_count: 77,
      cost: 66,
    });
    await render([fixture]);

    const text = cards()[0]?.textContent ?? "";
    for (const expected of [
      "真实课程封面",
      "AI 生视频",
      "完成",
      "最近更新",
      "zero.mp4",
      "0 B",
    ]) {
      expect(text).toContain(expected);
    }
    for (const forbidden of [
      "99",
      "88",
      "77",
      "66",
      "时长",
      "分辨率",
      "花费",
      "Invalid Date",
      "轮对话",
    ]) {
      expect(text).not.toContain(forbidden);
    }
    const card = cards()[0]!;
    expect(card.className).toContain("h-full");
    expect(card.className).toContain("flex-col");
    expect(card.firstElementChild?.className).toContain("aspect-video");
    expect(card.lastElementChild?.className).toContain("flex-1");
    expect(
      card.querySelector("[data-testid='work-card-actions']")?.className,
    ).toContain("mt-auto");
    expect(document.querySelector("img")?.getAttribute("src")).toBe(
      "/api/video/tasks/honest/thumbnail",
    );
    expect(
      document.querySelector("[data-testid='work-grid']")?.className,
    ).toContain("auto-rows-fr");
  });

  it("uses the download-response label for an existing unnamed legacy file", async () => {
    await render([makeTaskWork("legacy", { thumbnail_url: null })]);

    const text = cards()[0]?.textContent ?? "";
    expect(text).toContain("文件名由下载响应确定");
    expect(text).not.toContain("尚未交付文件");
  });

  it("opens only the selected work in a dialog without inline player growth", async () => {
    const works = [makeTaskWork("one"), makeTaskWork("two")];
    await render(works);
    const grid = document.querySelector("[data-testid='work-grid']");

    await act(async () => button("查看", cards()[1]).click());

    expect(cards()).toHaveLength(2);
    expect(grid?.querySelector("video")).toBeNull();
    expect(document.querySelector("video")?.getAttribute("src")).toBe(
      "/api/video/tasks/two/download",
    );
    expect(cards()[1]?.querySelector("a[download]")?.getAttribute("href")).toBe(
      "/api/video/tasks/two/download",
    );
  });

  it.each([
    ["running", "处理中"],
    ["composing", "合成中"],
    ["qc_repair", "质检修复中"],
    ["pending", "排队中"],
    ["cancelled", "已取消"],
  ])(
    "preserves %s status and blocks invalid media actions",
    async (status, label) => {
      await render([makeTaskWork(status, { status, progress: 40 })]);

      expect(cards()[0]?.textContent).toContain(label);
      expect(button("查看", cards()[0]).disabled).toBe(true);
      expect(cards()[0]?.querySelector("a[download]")).toBeNull();
      expect(button("详情", cards()[0])).toBeDefined();
      expect(button("删除", cards()[0])).toBeDefined();
      if (status === "cancelled") {
        const badge = cards()[0]?.querySelector(
          "[data-testid='work-status-badge']",
        );
        expect(badge?.className).toContain("bg-neutral-100");
        expect(
          badge?.querySelector("svg")?.classList.contains("animate-spin"),
        ).toBe(false);
      }
    },
  );

  it("keeps missing and incomplete-draft records visible without media or storyboard actions", async () => {
    const missing = makeTaskWork("missing", {
      artifact_missing: true,
      thumbnail_url: "/api/video/tasks/missing/thumbnail",
    });
    await render([missing]);
    expect(cards()[0]?.textContent).toContain("产物已清理");
    expect(document.querySelector("img")).toBeNull();
    expect(button("查看", cards()[0]).disabled).toBe(true);
    expect(cards()[0]?.querySelector("a[download]")).toBeNull();

    const draft = makeTaskWork("drafting", { status: "draft", progress: 40 });
    await render([draft]);
    expect(cards()[0]?.textContent).toContain("生成分镜");
    expect(cards()[0]?.textContent).not.toContain("继续编辑分镜");
    expect(button("查看", cards()[0]).disabled).toBe(true);
    expect(cards()[0]?.querySelector("a[download]")).toBeNull();
  });

  it("restores storyboard, details, per-task retry confirmation, and delete confirmation", async () => {
    const onDelete = vi.fn<(id: string) => void>();
    const onNavigate =
      vi.fn<(task: VideoTaskBrief, path?: "storyboard") => void>();
    const draft = makeTaskWork("draft", { status: "draft", progress: 100 });
    const failed = makeTaskWork("failed", {
      status: "failed",
      error_message: "真实失败原因必须完整可见",
    });
    const qc = makeTaskWork("qc", { status: "qc_repair", progress: 80 });
    await render([draft, failed, qc], { onDelete, onTaskNavigate: onNavigate });

    expect(cards()[1]?.textContent).toContain("真实失败原因必须完整可见");
    await act(async () => button("详情", cards()[0]).click());
    expect(onNavigate).toHaveBeenCalledWith(
      draft.source.kind === "video_task" ? draft.source.task : null,
    );
    await act(async () => button("继续编辑分镜", cards()[0]).click());
    expect(onNavigate).toHaveBeenCalledWith(
      draft.source.kind === "video_task" ? draft.source.task : null,
      "storyboard",
    );

    await act(async () => button("重试合成", cards()[1]).click());
    await act(async () => dialogButton("取消").click());
    expect(
      recompose.calls.reduce(
        (sum, call) => sum + call.mutate.mock.calls.length,
        0,
      ),
    ).toBe(0);
    await act(async () => button("重试合成", cards()[1]).click());
    await act(async () => dialogButton("确认合成").click());
    expect(
      recompose.calls
        .filter((call) => call.id === "failed")
        .reduce((sum, call) => sum + call.mutate.mock.calls.length, 0),
    ).toBe(1);
    expect(
      recompose.calls
        .filter((call) => call.id !== "failed")
        .reduce((sum, call) => sum + call.mutate.mock.calls.length, 0),
    ).toBe(0);

    await act(async () => button("删除", cards()[2]).click());
    await act(async () => dialogButton("取消").click());
    expect(onDelete).not.toHaveBeenCalled();
    await act(async () => button("删除", cards()[2]).click());
    await act(async () => dialogButton("删除").click());
    expect(onDelete).toHaveBeenCalledExactlyOnceWith("qc");
    expect(new Set(recompose.calls.map((call) => call.id))).toEqual(
      new Set(["draft", "failed", "qc"]),
    );
  });

  it.each(["not_delivered", "missing", "unknown"] as const)(
    "makes continue the only primary action for Agent %s",
    async (state) => {
      const files =
        state === "missing"
          ? [
              makeAgentFile("missing.pptx", {
                exists: false,
                size: null,
                modifiedAt: null,
                downloadUrl: null,
              }),
            ]
          : [];
      const onContinue = vi.fn<(work: WorkSummary) => void>();
      const work = makeAgentWork(state, files, { projectType: "course-ppt" });
      await render([work], { capability: "ppt-master", onContinue });

      expect(cards()[0]?.textContent).toContain("继续对话");
      expect(cards()[0]?.textContent).not.toContain("删除");
      expect(cards()[0]?.querySelector("a[download]")).toBeNull();
      expect(
        Array.from(cards()[0]?.querySelectorAll("button") ?? []).some(
          (item) => item.textContent === "查看",
        ),
      ).toBe(false);
      if (state === "unknown")
        expect(cards()[0]?.textContent).toContain("文件信息不可用");
      await act(async () => button("继续对话", cards()[0]).click());
      expect(onContinue).toHaveBeenCalledExactlyOnceWith(work);
    },
  );

  it("keeps legacy Agent aliases routable through canonical continue URLs", async () => {
    const navigations: string[] = [];
    const work = makeAgentWork("not_delivered", [], {
      projectType: "course-ppt",
    });
    await render([work], {
      capability: "ppt-master",
      onContinue: (selected) => {
        if (selected.source.kind === "agent") {
          navigations.push(pathOfThread(selected.source.thread));
        }
      },
    });

    await act(async () => button("继续对话", cards()[0]).click());

    expect(navigations).toEqual([
      "/workspace/agents/ppt-master/chats/thread-1",
    ]);
    expect(cards()[0]?.textContent).not.toContain("删除");
  });

  it("lets partial Agent works open available files without enabling missing downloads", async () => {
    const available = makeAgentFile("available.pptx");
    const missing = makeAgentFile("missing.pptx", {
      exists: false,
      size: null,
      modifiedAt: null,
      downloadUrl: null,
    });
    await render([makeAgentWork("partial", [available, missing])], {
      capability: "ppt-master",
    });

    expect(button("查看", cards()[0]).disabled).toBe(false);
    expect(cards()[0]?.querySelector("a[download]")?.getAttribute("href")).toBe(
      available.downloadUrl,
    );
    expect(cards()[0]?.textContent).toContain("部分文件可用");
  });

  it("uses English labels for cleaned artifacts and pending retry", async () => {
    ui.locale = "en-US";
    await render([makeTaskWork("missing", { artifact_missing: true })]);
    expect(cards()[0]?.textContent).toContain("Artifact cleaned");
    expect(cards()[0]?.textContent).not.toContain("产物已清理");

    ui.recomposePending = true;
    await render([makeTaskWork("failed", { status: "failed" })]);
    expect(cards()[0]?.textContent).toContain("Retrying…");
    expect(cards()[0]?.textContent).not.toContain("重试中…");
  });

  it("disables refresh while awaiting onRetry and always restores pending", async () => {
    let finish: (() => void) | undefined;
    const onRetry = vi.fn<() => Promise<void>>(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    await render([makeTaskWork("refresh")], { onRetry });

    await act(async () => button("刷新").click());
    expect(button("刷新中").disabled).toBe(true);
    await act(async () => button("刷新中").click());
    expect(onRetry).toHaveBeenCalledTimes(1);

    await act(async () => {
      finish?.();
      await Promise.resolve();
    });
    expect(button("刷新").disabled).toBe(false);
  });

  it("contains rejected refreshes and restores pending without losing works", async () => {
    const onRetry = vi
      .fn<() => Promise<void>>()
      .mockRejectedValue(new Error("refresh rejected"));
    await render([makeTaskWork("kept-after-rejection")], { onRetry });

    await act(async () => button("刷新").click());

    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(button("刷新").disabled).toBe(false);
    expect(cards()).toHaveLength(1);
  });
});
