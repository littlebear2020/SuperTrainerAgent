import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CompletedVideoGrid } from "@/components/workspace/video-gen/completed-video-grid";
import type { VideoTaskBrief } from "@/core/video-gen/types";

const recompose = vi.hoisted(() => ({
  calls: [] as Array<{ id: string; mutate: ReturnType<typeof vi.fn> }>,
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useRecomposeTask: (id: string) => {
    const mutate = vi.fn();
    recompose.calls.push({ id, mutate });
    return { mutate, isPending: false };
  },
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function task(
  id: string,
  overrides: Partial<VideoTaskBrief> = {},
): VideoTaskBrief {
  return {
    id,
    user_id: "u1",
    domain: "video_gen",
    task_type: "ai_image",
    status: "ready",
    progress: 100,
    title: `作品 ${id}`,
    error_message: null,
    retry_count: 0,
    dirty_frames: [],
    created_at: "2026-11-01T08:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    thumbnail_url: `/api/video/tasks/${id}/thumbnail`,
    cost: null,
    currency: null,
    ...overrides,
  };
}

function button(text: string): HTMLButtonElement {
  const result = Array.from(document.querySelectorAll("button")).find((item) =>
    item.textContent?.includes(text),
  );
  if (!result) throw new Error(`button not found: ${text}`);
  return result;
}

function dialogButton(text: string): HTMLButtonElement {
  const dialog = document.querySelector("[role='alertdialog']");
  const result = Array.from(dialog?.querySelectorAll("button") ?? []).find(
    (item) => item.textContent === text,
  );
  if (!result) throw new Error(`dialog button not found: ${text}`);
  return result;
}

describe("CompletedVideoGrid", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
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

  async function render(
    tasks: VideoTaskBrief[],
    props: {
      workType?: "ai_image" | "ai_video";
      isLoading?: boolean;
      isError?: boolean;
      onRetry?: () => void;
      onDelete?: (id: string) => void;
      onNavigate?: (id: string, path?: "storyboard") => void;
    } = {},
  ) {
    await act(async () => {
      root.render(
        <CompletedVideoGrid
          workType={props.workType ?? "ai_image"}
          tasks={tasks}
          onDelete={props.onDelete ?? vi.fn()}
          onNavigate={props.onNavigate ?? vi.fn()}
          {...props}
        />,
      );
    });
  }

  it("renders six then loads six more without another request", async () => {
    const onRetry = vi.fn();
    await render(
      Array.from({ length: 12 }, (_, i) => task(String(i + 1))),
      {
        onRetry,
      },
    );
    expect(
      container.querySelectorAll("[data-testid='completed-work-card']"),
    ).toHaveLength(6);
    const grid = container.querySelector("[data-testid='completed-work-grid']");
    for (const className of [
      "grid-cols-1",
      "sm:grid-cols-2",
      "xl:grid-cols-3",
    ]) {
      expect(grid?.className).toContain(className);
    }
    expect(grid?.className).toContain("justify-start");
    await act(async () => button("加载更多").click());
    expect(
      container.querySelectorAll("[data-testid='completed-work-card']"),
    ).toHaveLength(12);
    expect(
      Array.from(container.querySelectorAll("button")).some(
        (item) => item.textContent === "加载更多",
      ),
    ).toBe(false);
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("shows only honest title type status update and cover", async () => {
    const fixture = {
      ...task("1", { title: "真实课程封面" }),
      total_duration: 99,
      frame_count: 88,
      shot_count: 77,
      cost: 66,
      video_count: 99,
    } as VideoTaskBrief;
    await render([fixture]);
    const text = container.textContent ?? "";
    for (const expected of ["真实课程封面", "AI 生图", "完成", "最近更新"]) {
      expect(text).toContain(expected);
    }
    expect(container.querySelector("img")?.getAttribute("src")).toBe(
      "/api/video/tasks/1/thumbnail",
    );
    const card = container.querySelector<HTMLElement>(
      "[data-testid='completed-work-card']",
    )!;
    for (const className of ["h-full", "flex", "flex-col"]) {
      expect(card.className).toContain(className);
    }
    expect(card.firstElementChild?.className).toContain("aspect-video");
    expect(card.firstElementChild?.className).toContain("shrink-0");
    const body = card.lastElementChild;
    for (const className of ["flex", "flex-1", "flex-col"]) {
      expect(body?.className).toContain(className);
    }
    expect(button("查看").parentElement?.className).toContain("mt-auto");
    for (const forbidden of [
      "99",
      "88",
      "77",
      "66",
      "时长",
      "分辨率",
      "完成时间",
      "项目统计",
      "花费",
      "系统健康",
      "Invalid Date",
    ]) {
      expect(text).not.toContain(forbidden);
    }
  });

  it("missing artifact stays visible but cannot view or download", async () => {
    await render([
      task("1", {
        artifact_missing: true,
        thumbnail_url: "/api/video/tasks/1/thumbnail",
      }),
    ]);
    expect(container.textContent).toContain("产物已清理");
    expect(container.querySelector("img")).toBeNull();
    expect(button("查看").disabled).toBe(true);
    expect(container.querySelector("a[download]")).toBeNull();
    await act(async () => button("查看").click());
    expect(container.querySelector("video")).toBeNull();
  });

  it("view expands only the selected ready work and download uses existing endpoint", async () => {
    await render([task("1"), task("2")]);
    const secondCard = container.querySelector("[data-task-id='2']")!;
    await act(async () => {
      Array.from(secondCard.querySelectorAll("button"))
        .find((item) => item.textContent === "查看")
        ?.click();
    });
    const videos = container.querySelectorAll("video");
    expect(videos).toHaveLength(1);
    expect(videos[0]?.getAttribute("src")).toBe("/api/video/tasks/2/download");
    expect(videos[0]?.getAttribute("preload")).toBe("none");
    const download = secondCard.querySelector("a[download]");
    expect(download?.getAttribute("href")).toBe("/api/video/tasks/2/download");
  });

  it("playback error replaces player with honest fallback", async () => {
    await render([
      task("1", { title: null, updated_at: null, created_at: null }),
    ]);
    expect(container.textContent).toContain("未命名作品");
    expect(container.textContent).toContain("更新时间未知");
    await act(async () => button("查看").click());
    const video = container.querySelector("video")!;
    await act(async () => video.dispatchEvent(new Event("error")));
    expect(container.querySelector("video")).toBeNull();
    expect(container.textContent).toContain("视频预览暂不可用");
    expect(container.querySelector("a[download]")).not.toBeNull();
    expect(container.textContent).not.toContain("Invalid Date");
  });

  it("keeps loading empty and error states distinct", async () => {
    await render([], { isLoading: true });
    expect(container.textContent).toContain("正在加载作品");
    await render([]);
    expect(container.textContent).toContain("暂无AI 生图作品");
    const onRetry = vi.fn();
    await render([], { isError: true, onRetry });
    expect(container.textContent).toContain("作品加载失败");
    expect(container.textContent).not.toContain("暂无AI 生图作品");
    await act(async () => button("重试").click());
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("follows controlled work type and resets preview and pagination without internal tabs", async () => {
    const tasks = [
      ...Array.from({ length: 7 }, (_, index) =>
        task(String(index + 1), {
          task_type: index === 6 ? "ai_visual" : "ai_image",
        }),
      ),
      ...Array.from({ length: 7 }, (_, index) =>
        task(String(101 + index), { task_type: "ai_video" }),
      ),
    ];
    await render(tasks, { workType: "ai_image" });
    expect(
      container.querySelectorAll("[data-testid='completed-work-card']"),
    ).toHaveLength(6);
    expect(container.textContent).not.toContain("作品 101");
    await act(async () => button("查看").click());
    expect(container.querySelector("video")).not.toBeNull();

    await render(tasks, { workType: "ai_video" });
    expect(container.querySelector("video")).toBeNull();
    expect(container.textContent).toContain("作品 101");
    expect(container.textContent).not.toContain("作品 1 ");
    expect(
      container.querySelectorAll("[data-testid='completed-work-card']"),
    ).toHaveLength(6);
    await act(async () => button("加载更多").click());
    expect(
      container.querySelectorAll("[data-testid='completed-work-card']"),
    ).toHaveLength(7);

    await render(tasks, { workType: "ai_image" });
    expect(
      container.querySelectorAll("[data-testid='completed-work-card']"),
    ).toHaveLength(6);
    expect(container.querySelector("[role='tablist']")).toBeNull();
  });

  it.each([
    ["running", "处理中"],
    ["composing", "重新合成中"],
    ["qc_repair", "质检修复中"],
    ["pending", "排队中"],
    ["cancelled", "排队中"],
  ])("preserves %s status and safe actions", async (status, label) => {
    await render([task(status, { status, progress: 40 })]);
    const card = container.querySelector<HTMLElement>(
      "[data-testid='completed-work-card']",
    )!;
    expect(card.textContent).toContain(label);
    expect(button("查看").disabled).toBe(true);
    expect(card.querySelector("a[download]")).toBeNull();
    expect(button("详情")).toBeDefined();
    expect(button("删除")).toBeDefined();
  });

  it("keeps an incomplete draft free of playback download and storyboard actions", async () => {
    await render([task("drafting", { status: "draft", progress: 40 })]);
    expect(container.textContent).toContain("生成分镜");
    expect(button("查看").disabled).toBe(true);
    expect(container.querySelector("a[download]")).toBeNull();
    expect(container.textContent).not.toContain("继续编辑分镜");
    expect(button("详情")).toBeDefined();
    expect(button("删除")).toBeDefined();
  });

  it("restores details storyboard retry confirmation and delete confirmation by exact id", async () => {
    const onDelete = vi.fn();
    const onNavigate = vi.fn();
    await render(
      [
        task("draft", { status: "draft", progress: 100 }),
        task("failed-visual", {
          status: "failed",
          task_type: "ai_visual",
          error_message: "真实失败原因",
        }),
        task("qc", { status: "qc_repair", progress: 80 }),
      ],
      { onDelete, onNavigate },
    );
    const draft = container.querySelector("[data-task-id='draft']")!;
    await act(async () => {
      Array.from(draft.querySelectorAll("button"))
        .find((item) => item.textContent === "详情")
        ?.click();
    });
    expect(onNavigate).toHaveBeenCalledWith("draft");
    await act(async () => button("继续编辑分镜").click());
    expect(onNavigate).toHaveBeenCalledWith("draft", "storyboard");

    const failed = container.querySelector("[data-task-id='failed-visual']")!;
    expect(failed.textContent).toContain("真实失败原因");
    await act(async () => {
      Array.from(failed.querySelectorAll("button"))
        .find((item) => item.textContent === "重试合成")
        ?.click();
    });
    await act(async () => dialogButton("取消").click());
    expect(
      recompose.calls.filter((call) => call.id === "failed-visual").at(-1)
        ?.mutate,
    ).not.toHaveBeenCalled();
    await act(async () => {
      Array.from(failed.querySelectorAll("button"))
        .find((item) => item.textContent === "重试合成")
        ?.click();
    });
    await act(async () => dialogButton("确认合成").click());
    expect(
      recompose.calls
        .filter((call) => call.id === "failed-visual")
        .reduce((total, call) => total + call.mutate.mock.calls.length, 0),
    ).toBe(1);

    const qc = container.querySelector("[data-task-id='qc']")!;
    expect(qc.textContent).toContain("质检修复");
    expect(qc.textContent).not.toContain("重试合成");
    expect(qc.querySelector("a[download]")).toBeNull();
    expect(
      Array.from(qc.querySelectorAll("button")).find(
        (item) => item.textContent === "查看",
      )?.disabled,
    ).toBe(true);
    await act(async () => {
      qc.querySelector<HTMLButtonElement>("button[aria-label='删除']")?.click();
    });
    await act(async () => dialogButton("取消").click());
    expect(onDelete).not.toHaveBeenCalled();
    await act(async () => {
      qc.querySelector<HTMLButtonElement>("button[aria-label='删除']")?.click();
    });
    await act(async () => dialogButton("删除").click());
    expect(onDelete).toHaveBeenCalledExactlyOnceWith("qc");
  });
});
