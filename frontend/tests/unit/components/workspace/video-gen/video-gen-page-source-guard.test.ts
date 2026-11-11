import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const page = readFileSync(
  "src/components/workspace/video-gen/video-gen-page.tsx",
  "utf8",
);
const grid = readFileSync(
  "src/components/workspace/video-gen/completed-video-grid.tsx",
  "utf8",
);
const thumbnail = readFileSync(
  "src/components/workspace/video-gen/task-thumbnail.tsx",
  "utf8",
);

describe("video-gen-page extraction source guards", () => {
  it("re-exports TaskThumbnail without moving recompose code", () => {
    expect(page).toContain('export { TaskThumbnail } from "./task-thumbnail"');
    expect(page).not.toContain("export function TaskThumbnail(");
    const taskCard = page.indexOf("export function TaskCard");
    expect(taskCard).toBeGreaterThanOrEqual(0);
    const taskCardSource = page.slice(taskCard);
    for (const source of [
      "useRecomposeTask(task.id",
      "recomposeActionFor(task)",
      "recomposeMut.mutate()",
      "RecomposeConfirmDialog",
    ]) {
      expect(taskCardSource).toContain(source);
    }
    expect(thumbnail.toLowerCase()).not.toContain("recompose");
  });

  it("wires works operations without moving per-card hooks into a map", () => {
    expect(page).toContain("useDeleteVideoTask()");
    expect(page).toContain("onDelete={");
    expect(page).toContain("onNavigate={");
    expect(grid).toContain("function CompletedWorkCard(");
    expect(grid).toContain("useRecomposeTask(task.id");
    expect(grid).toContain("videoTaskTypeLabel(task.task_type)");
    expect(grid).not.toContain("function workTypeLabel(");
    expect(grid).not.toContain(".map((task) => useRecomposeTask");
  });
});
