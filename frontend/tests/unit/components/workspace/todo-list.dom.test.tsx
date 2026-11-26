import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { TodoList } from "@/components/workspace/todo-list";
import type { Todo } from "@/core/todos";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function item(label: string): HTMLElement {
  return Array.from(document.querySelectorAll<HTMLElement>("span")).find(
    (node) => node.textContent === label,
  )!;
}

describe("TodoList", () => {
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

  async function render(todos: Todo[]) {
    await act(async () => {
      root.render(<TodoList todos={todos} collapsed={false} />);
    });
  }

  // 后端在运行中途推出 `[进行中, 待办] → [完成, 进行中]` 的快照时，界面必须跟着换状态：
  // 状态切换只改 className，不重挂节点——这里钉住「不依赖终态快照也能刷新」。
  it("rerenders status changes between snapshots", async () => {
    await render([
      { content: "Step A", status: "in_progress" },
      { content: "Step B", status: "pending" },
    ]);
    expect(item("Step A").className).toContain("text-primary/70");
    expect(item("Step A").className).not.toContain("line-through");
    expect(item("Step B").className).not.toContain("text-primary/70");

    await render([
      { content: "Step A", status: "completed" },
      { content: "Step B", status: "in_progress" },
    ]);
    expect(item("Step A").className).toContain("line-through");
    expect(item("Step A").className).not.toContain("text-primary/70");
    expect(item("Step B").className).toContain("text-primary/70");
    expect(item("Step B").className).not.toContain("line-through");
  });
});
