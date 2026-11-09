import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CompletedVideoGrid } from "@/components/workspace/video-gen/completed-video-grid";
import type { VideoTaskBrief } from "@/core/video-gen/types";

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
    created_at: `2026-11-${id.padStart(2, "0")}T08:00:00Z`,
    updated_at: `2026-11-${id.padStart(2, "0")}T09:00:00Z`,
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

describe("CompletedVideoGrid", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
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
      isLoading?: boolean;
      isError?: boolean;
      onRetry?: () => void;
    } = {},
  ) {
    await act(async () => {
      root.render(<CompletedVideoGrid tasks={tasks} {...props} />);
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

  it("shows only honest title type ready update and cover", async () => {
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
    for (const expected of ["真实课程封面", "图片动效", "完成", "最近更新"]) {
      expect(text).toContain(expected);
    }
    expect(container.querySelector("img")?.getAttribute("src")).toBe(
      "/api/video/tasks/1/thumbnail",
    );
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
    expect(container.textContent).toContain("暂无已完成作品");
    const onRetry = vi.fn();
    await render([], { isError: true, onRetry });
    expect(container.textContent).toContain("作品加载失败");
    expect(container.textContent).not.toContain("暂无已完成作品");
    await act(async () => button("重试").click());
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
