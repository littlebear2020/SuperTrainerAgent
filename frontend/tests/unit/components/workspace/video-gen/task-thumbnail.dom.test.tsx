import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { TaskThumbnail } from "@/components/workspace/video-gen/task-thumbnail";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("TaskThumbnail extracted component", () => {
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
  });

  async function render(props: {
    url: string | null;
    artifactMissing?: boolean;
  }) {
    await act(async () => {
      root.render(
        <TaskThumbnail
          {...props}
          className="aspect-video h-auto w-full rounded-t-2xl"
        />,
      );
    });
  }

  it("supports large card class without changing fallback semantics", async () => {
    await render({ url: "/api/video/tasks/a/thumbnail" });
    const image = container.querySelector<HTMLImageElement>(
      "[data-testid='thumbnail-img']",
    );
    expect(image?.className).toContain("aspect-video h-auto w-full");
    await act(async () => image?.dispatchEvent(new Event("error")));
    expect(
      container.querySelector("[data-testid='thumbnail-placeholder']")?.className,
    ).toContain("aspect-video h-auto w-full");

    await render({ url: "/api/video/tasks/b/thumbnail" });
    expect(
      container.querySelector<HTMLImageElement>(
        "[data-testid='thumbnail-img']",
      )?.src,
    ).toContain("/api/video/tasks/b/thumbnail");

    await render({ url: "/private/anchor", artifactMissing: true });
    const missing = container.querySelector(
      "[data-testid='thumbnail-artifact-missing']",
    );
    expect(missing?.textContent).toContain("产物已清理");
    expect(missing?.className).toContain("aspect-video h-auto w-full");
    expect(container.querySelector("img")).toBeNull();

    await render({ url: null });
    expect(
      container.querySelector("[data-testid='thumbnail-placeholder']"),
    ).not.toBeNull();
  });
});
